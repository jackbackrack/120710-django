/*
 * 3D Gallery Viewer — Three.js ES module
 * World units = inches.  Origin = room centre, floor at y=0.
 * X east (+), Y up (+), Z south (+).
 */
import * as THREE from 'three';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';

const IN2M = 0.0254;           // 1 inch in metres (Three uses metres by default; we work in inches and convert)

// Escape user-controlled text (artwork title/medium/dims/artist names) before it
// goes into the placard innerHTML. This viewer is public, so an unescaped title
// like `<img src=x onerror=…>` would be stored XSS.
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const cfg        = window.ROOM_CONFIG;   // { width_in, depth_in, height_in }
const placements = window.PLACEMENTS;   // [{ artwork, wall, x_in, y_in, z_in }]

const W  = cfg.width_in  * IN2M;   // room width  (E–W)
const D  = cfg.depth_in  * IN2M;   // room depth  (N–S)
const H  = cfg.height_in * IN2M;   // room height
const HW = W / 2, HD = D / 2;

// ── Renderer ─────────────────────────────────────────────────────────────────
const canvas = document.getElementById('viewer-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
// Cap pixel ratio: on a 2×/3× retina display, full DPR renders 4–9× the fragments
// for little visible gain in a gallery walkthrough.  1.5 keeps edges smooth cheaply.
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
function setSize() {
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  camera.aspect = canvas.clientWidth / canvas.clientHeight;
  camera.updateProjectionMatrix();
}

// ── Scene ─────────────────────────────────────────────────────────────────────
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xddd8d0);

// ── Camera ────────────────────────────────────────────────────────────────────
const camera = new THREE.PerspectiveCamera(75, 1, 0.01, 100);
const EYE_H = 60 * IN2M;   // 60" eye height
camera.position.set(0, EYE_H, 0);

// ── Lighting ──────────────────────────────────────────────────────────────────
scene.add(new THREE.AmbientLight(0xffffff, 1.2));

// Track lighting — four warm points near ceiling
[[HW * 0.4, H * 0.95, 0], [-HW * 0.4, H * 0.95, 0],
 [0, H * 0.95, HD * 0.3], [0, H * 0.95, -HD * 0.3]].forEach(function ([x, y, z]) {
  const pt = new THREE.PointLight(0xfff5e0, 1.0, 30);
  pt.position.set(x, y, z);
  scene.add(pt);
});

// ── Room geometry ─────────────────────────────────────────────────────────────
// Inside-out box: BackSide renders the inner faces visible from inside the room.
// BoxGeometry face order: +X(E), -X(W), +Y(ceil), -Y(floor), +Z(S), -Z(N)
const textureLoader = new THREE.TextureLoader();

function makeSurfaceMat(color) {
  return new THREE.MeshBasicMaterial({ color: color, side: THREE.BackSide });
}

const matE    = makeSurfaceMat(0xf0ece4);
const matW    = makeSurfaceMat(0xf0ece4);
const matCeil = makeSurfaceMat(0xfaf8f4);
const matFloor= makeSurfaceMat(0xb8a890);
const matS    = makeSurfaceMat(0xf0ece4);
const matN    = makeSurfaceMat(0xf0ece4);

// BoxGeometry face order: +X(E), -X(W), +Y(ceil), -Y(floor), +Z(S), -Z(N)
const room = new THREE.Mesh(
  new THREE.BoxGeometry(W, H, D),
  [matE, matW, matCeil, matFloor, matS, matN]
);
room.position.y = H / 2;   // floor at y=0, ceiling at y=H
scene.add(room);

// Apply wall/floor/ceiling textures if configured
(function () {
  var surfaces = [
    { url: cfg.wall_e_img,  mat: matE    },
    { url: cfg.wall_w_img,  mat: matW    },
    { url: cfg.ceiling_img, mat: matCeil },
    { url: cfg.floor_img,   mat: matFloor},
    { url: cfg.wall_s_img,  mat: matS    },
    { url: cfg.wall_n_img,  mat: matN    },
  ];
  surfaces.forEach(function (s) {
    if (!s.url) return;
    textureLoader.load(s.url, function (tex) {
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.wrapS = THREE.RepeatWrapping;
      tex.wrapT = THREE.RepeatWrapping;
      s.mat.map = tex;
      s.mat.color.set(0xffffff);
      s.mat.needsUpdate = true;
    }, undefined, function () {
      console.warn('3D viewer: failed to load wall texture', s.url);
    });
  });
}());

// Subtle floor grid for spatial reference
const gridHelper = new THREE.GridHelper(Math.max(W, D), 20, 0x999988, 0xbbbb99);
gridHelper.position.y = 0.001;
scene.add(gridHelper);

// ── Artwork planes ────────────────────────────────────────────────────────────
const WALL_OFFSET = 0.005; // 5 mm gap from wall surface

function wallNormal(wall) {
  // unit normal pointing inward (toward room centre)
  if (wall === 'N') return new THREE.Vector3(0, 0,  1);
  if (wall === 'S') return new THREE.Vector3(0, 0, -1);
  if (wall === 'E') return new THREE.Vector3(-1, 0, 0);
  if (wall === 'W') return new THREE.Vector3(1, 0,  0);
  if (wall === 'ceiling') return new THREE.Vector3(0, -1, 0);
  return new THREE.Vector3(0, 1, 0);  // floor
}

function placementPosition(p) {
  return new THREE.Vector3(p.x_in * IN2M, p.y_in * IN2M, p.z_in * IN2M);
}

function wallQuaternion(wall) {
  // PlaneGeometry default normal is +Z.  We need each plane to face inward.
  // Ry(+π/2) rotates +Z → +X (outward for East) — so East needs Ry(-π/2) → -X (inward).
  // West needs Ry(+π/2) → +X (inward from west wall at -W/2).
  const q = new THREE.Quaternion();
  if (wall === 'N') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0);
  else if (wall === 'S') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
  else if (wall === 'E') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -Math.PI / 2);
  else if (wall === 'W') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0),  Math.PI / 2);
  else if (wall === 'ceiling') q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
  else q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
  return q;
}

// Placard data for hover
const artworkMeshes = [];

function loadTex(url, onLoad) {
  // url comes from Artwork.layout_display_url, which already resolves to the crop
  // or the hero — a guaranteed-valid image — so no extra fallback is needed here.
  textureLoader.load(url, function (tex) {
    tex.colorSpace = THREE.SRGBColorSpace;
    onLoad(tex);
  }, undefined, function () {
    console.warn('3D viewer: failed to load texture', url);
  });
}

// A plane sized to best-fit (contain) the image within a w×h face — the image
// keeps its aspect ratio and is letterboxed against whatever is behind it.
function bestFitImagePlane(tex, w, h) {
  var ia = (tex.image && tex.image.width && tex.image.height)
    ? tex.image.width / tex.image.height : (w / h);
  var fa = w / h, pw, ph;
  if (!isFinite(ia) || ia <= 0) { pw = w; ph = h; }
  else if (ia > fa)             { pw = w; ph = w / ia; }
  else                          { ph = h; pw = h * ia; }
  return new THREE.Mesh(
    new THREE.PlaneGeometry(pw, ph),
    new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide })
  );
}

// Flat piece (depth 0): a single plane on the wall/floor/ceiling.
function buildFlatPlane(p, art, aw, ah, norm) {
  var geo  = new THREE.PlaneGeometry(aw, ah);
  var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x999999, side: THREE.DoubleSide }));
  var pos  = placementPosition(p);
  pos.addScaledVector(norm, WALL_OFFSET);
  mesh.position.copy(pos);
  mesh.quaternion.copy(wallQuaternion(p.wall));
  if (art.img) {
    loadTex(art.img, function (tex) {
      var ia = tex.image.width / tex.image.height;
      if (!isFinite(ia) || ia <= 0) return;
      mesh.geometry.dispose();
      mesh.geometry = new THREE.PlaneGeometry(ah * ia, ah);
      var fr = mesh.children[0];
      if (fr) { fr.geometry.dispose(); fr.geometry = new THREE.EdgesGeometry(mesh.geometry); }
      mesh.material = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide });
    });
  }
  mesh.userData = { art: art, wall: p.wall, rotation: p.rotation || 0 };
  scene.add(mesh);
  artworkMeshes.push(mesh);
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),
                                  new THREE.MeshBasicMaterial({ color: 0x333333 })));
}

// 3D piece (depth > 0): a cuboid (w × h × d). Floor/ceiling pieces get the image
// best-fit on ALL SIX faces; wall pieces get the two w×h faces (rear one ends up
// inside the wall). A face is skipped if it's a thin sliver — its shorter side
// under 1/8 of its longer side. Local axes: X=width, Y=height, Z=depth.
function buildCuboid(p, art, aw, ah, ad, norm) {
  var box = new THREE.Mesh(
    new THREE.BoxGeometry(aw, ah, ad),
    new THREE.MeshBasicMaterial({ color: 0xdddddd })
  );
  var base = placementPosition(p);
  var isFloorCeil = (p.wall === 'floor' || p.wall === 'ceiling');
  if (isFloorCeil) {
    // Stand upright: footprint w×d on the surface, height h; identity orientation.
    var cy = (p.wall === 'ceiling') ? (H - ah / 2) : (p.y_in * IN2M + ah / 2);  // base at y_in (raised on a pedestal)
    box.position.set(base.x, cy, base.z);
    if (p.rotation) box.rotation.y = ((p.rotation % 360) * Math.PI) / 180;   // yaw 0/90/180/270
  } else {
    // Depth extends inward from the wall; back face flush with the wall surface.
    box.position.copy(base.clone().addScaledVector(norm, WALL_OFFSET + ad / 2));
    box.quaternion.copy(wallQuaternion(p.wall));
    // A 3D object resting on a shelf can be turned about the vertical axis. The
    // wall quaternion is itself a yaw, so an extra rotateY composes cleanly.
    if (p.support != null && p.rotation) box.rotateY(((p.rotation % 360) * Math.PI) / 180);
  }
  box.userData = { art: art, wall: p.wall, rotation: p.rotation || 0 };
  scene.add(box);
  artworkMeshes.push(box);
  box.add(new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry),
                                 new THREE.MeshBasicMaterial({ color: 0x333333 })));

  var eps = 0.004;   // sit each image plane just off its face
  // Each face: image dims (fw × fh), local position, and rotation to face outward.
  var faces = isFloorCeil ? [
    { fw: aw, fh: ah, pos: [0, 0,  ad / 2 + eps], rot: [0, 0, 0] },            // +Z
    { fw: aw, fh: ah, pos: [0, 0, -ad / 2 - eps], rot: [0, Math.PI, 0] },      // -Z
    { fw: ad, fh: ah, pos: [ aw / 2 + eps, 0, 0], rot: [0,  Math.PI / 2, 0] }, // +X
    { fw: ad, fh: ah, pos: [-aw / 2 - eps, 0, 0], rot: [0, -Math.PI / 2, 0] }, // -X
    { fw: aw, fh: ad, pos: [0,  ah / 2 + eps, 0], rot: [-Math.PI / 2, 0, 0] }, // +Y (top)
    { fw: aw, fh: ad, pos: [0, -ah / 2 - eps, 0], rot: [ Math.PI / 2, 0, 0] }, // -Y (bottom)
  ] : [
    { fw: aw, fh: ah, pos: [0, 0,  ad / 2 + eps], rot: [0, 0, 0] },
    { fw: aw, fh: ah, pos: [0, 0, -ad / 2 - eps], rot: [0, Math.PI, 0] },
  ];

  if (art.img) {
    loadTex(art.img, function (tex) {
      faces.forEach(function (f) {
        // Skip thin sliver faces (shorter side < 1/8 of the longer side).
        if (Math.min(f.fw, f.fh) < Math.max(f.fw, f.fh) / 8) return;
        var plane = bestFitImagePlane(tex, f.fw, f.fh);
        plane.position.set(f.pos[0], f.pos[1], f.pos[2]);
        plane.rotation.set(f.rot[0], f.rot[1], f.rot[2]);
        box.add(plane);
      });
    });
  }
}

function buildArtwork(p) {
  var art  = p.artwork;
  var aw   = art.w_in * IN2M;
  var ah   = art.h_in * IN2M;
  var ad   = (art.d_in || 0) * IN2M;
  var norm = wallNormal(p.wall);
  if (ad > 0.0025) {   // > ~0.1" of depth → render as a 3D cuboid
    buildCuboid(p, art, aw, ah, ad, norm);
  } else {
    buildFlatPlane(p, art, aw, ah, norm);
  }
}

placements.forEach(buildArtwork);

// Final world position of a piece's mesh (matches what buildFlatPlane/buildCuboid
// set), so a plain move can be applied in place without reloading textures.
function artworkWorldPos(p) {
  var art = p.artwork;
  var ah = art.h_in * IN2M, ad = (art.d_in || 0) * IN2M;
  var base = placementPosition(p);
  var norm = wallNormal(p.wall);
  var isFloorCeil = (p.wall === 'floor' || p.wall === 'ceiling');
  var isCuboid = ad > 0.0025;
  if (isFloorCeil && isCuboid) {
    var cy = (p.wall === 'ceiling') ? (H - ah / 2) : (p.y_in * IN2M + ah / 2);  // base at y_in (raised on a pedestal)
    return new THREE.Vector3(base.x, cy, base.z);
  }
  if (isCuboid) return base.clone().addScaledVector(norm, WALL_OFFSET + ad / 2);
  return base.clone().addScaledVector(norm, WALL_OFFSET);
}

function removeArtworkMesh(mesh) {
  scene.remove(mesh);
  var i = artworkMeshes.indexOf(mesh);
  if (i !== -1) artworkMeshes.splice(i, 1);
  mesh.traverse(function (o) {
    if (o.geometry) o.geometry.dispose();
    var mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    mats.forEach(function (m) { if (m.map) m.map.dispose(); if (m.dispose) m.dispose(); });
  });
}

// ── Placards (5"×3" wall labels) ─────────────────────────────────────────────
// A physical label to the right of each wall-hung piece (2" gap, bottoms aligned),
// facing the same way as the piece. Click it in 3D to expand it full screen.
var placardMeshes = [];
var PLACARD_W_IN = 5, PLACARD_H_IN = 3, PLACARD_GAP_IN = 2;

// Support lookup so a piece on a pedestal can size its placard to the pedestal.
var supportsById = {};
(window.SUPPORTS || []).forEach(function (s) { supportsById[s.id] = s; });

// "Right of the piece" in world space, matching the 2D layout's per-wall mapping.
function wallRightDir(wall) {
  if (wall === 'N' || wall === 'S') return new THREE.Vector3(1, 0, 0);
  if (wall === 'E') return new THREE.Vector3(0, 0, 1);
  return new THREE.Vector3(0, 0, -1);   // W
}

function placardWorldPos(p) {
  var art = p.artwork;
  var pw = PLACARD_W_IN * IN2M, ph = PLACARD_H_IN * IN2M, gap = PLACARD_GAP_IN * IN2M;
  if (p.wall !== 'floor' && p.wall !== 'ceiling') {
    // Vertical wall: on the wall to the right, bottom aligned with the piece's
    // bottom (which equals the shelf top when the piece rests on a shelf).
    var aw = art.w_in * IN2M, ah = art.h_in * IN2M;
    var base = placementPosition(p);
    var pos = base.clone().addScaledVector(wallRightDir(p.wall), aw / 2 + gap + pw / 2);
    pos.y = base.y - ah / 2 + ph / 2;
    return pos.addScaledVector(wallNormal(p.wall), WALL_OFFSET);
  }
  // Floor / ceiling: a flat card lying on the surface, to the +x side of the
  // pedestal (or the piece if none), its far (+z) edge aligned with the
  // reference's far edge — read from above.
  var cx, cz, ex, ez;
  var s = (p.support != null) ? supportsById[p.support] : null;
  if (s) {
    cx = s.x_in; cz = s.z_in;
    var sw = (((s.rotation || 0) % 180) === 90);
    ex = sw ? s.d_in : s.w_in; ez = sw ? s.w_in : s.d_in;
  } else {
    cx = p.x_in; cz = p.z_in;
    var depth = (art.d_in && art.d_in > 0) ? art.d_in : art.h_in;
    var pr = (((p.rotation || 0) % 180) === 90);
    ex = pr ? depth : art.w_in; ez = pr ? art.w_in : depth;
  }
  var px = (cx + ex / 2 + PLACARD_GAP_IN + PLACARD_W_IN / 2) * IN2M;
  var pz = (cz + ez / 2 - PLACARD_H_IN / 2) * IN2M;
  var py = (p.wall === 'ceiling') ? (H - WALL_OFFSET) : WALL_OFFSET;
  return new THREE.Vector3(px, py, pz);
}

function _plTrunc(g, text, maxW) {
  if (g.measureText(text).width <= maxW) return text;
  var s = text;
  while (s.length > 1 && g.measureText(s + '…').width > maxW) s = s.slice(0, -1);
  return s + '…';
}
function makePlacardTexture(art) {
  var cw = 500, ch = 300, pad = 26;
  var cv = document.createElement('canvas'); cv.width = cw; cv.height = ch;
  var g = cv.getContext('2d');
  g.fillStyle = '#fff'; g.fillRect(0, 0, cw, ch);
  g.strokeStyle = '#333'; g.lineWidth = 6; g.strokeRect(3, 3, cw - 6, ch - 6);
  g.textBaseline = 'top';
  var x = pad, y = pad, maxW = cw - pad * 2;
  g.fillStyle = '#111'; g.font = 'bold 36px sans-serif';
  g.fillText(_plTrunc(g, art.name || '', maxW), x, y); y += 46;
  if (art.artists) { g.fillStyle = '#333'; g.font = '28px sans-serif';
    g.fillText(_plTrunc(g, art.artists, maxW), x, y); y += 38; }
  g.fillStyle = '#555'; g.font = '24px sans-serif';
  var meta = [];
  if (art.year)   meta.push(String(art.year));
  if (art.medium) meta.push(art.medium);
  if (art.dims)   meta.push(art.dims);
  if (meta.length) { g.fillText(_plTrunc(g, meta.join(', '), maxW), x, y); y += 34; }
  if (art.price) { g.fillStyle = '#111'; g.font = 'bold 24px sans-serif';
    g.fillText(_plTrunc(g, art.price + (art.sold ? ' — sold' : ''), maxW), x, y); }
  var tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  return tex;
}
function buildPlacard(p) {
  var geo = new THREE.PlaneGeometry(PLACARD_W_IN * IN2M, PLACARD_H_IN * IN2M);
  var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: makePlacardTexture(p.artwork), side: THREE.DoubleSide }));
  mesh.position.copy(placardWorldPos(p));
  mesh.quaternion.copy(wallQuaternion(p.wall));
  mesh.userData = { placard: p.artwork, wall: p.wall };
  scene.add(mesh);
  placardMeshes.push(mesh);
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),
                                  new THREE.MeshBasicMaterial({ color: 0x333333 })));
}
function removePlacardMesh(mesh) {
  scene.remove(mesh);
  var i = placardMeshes.indexOf(mesh);
  if (i !== -1) placardMeshes.splice(i, 1);
  mesh.traverse(function (o) {
    if (o.geometry) o.geometry.dispose();
    var mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    mats.forEach(function (m) { if (m.map) m.map.dispose(); if (m.dispose) m.dispose(); });
  });
}
function syncPlacards(list) {
  var byId = {};
  placardMeshes.forEach(function (m) { if (m.userData.placard) byId[m.userData.placard.id] = m; });
  var seen = {};
  list.forEach(function (p) {
    var id = p.artwork.id; seen[id] = true;
    var mesh = byId[id];
    if (mesh && mesh.userData.wall === p.wall) mesh.position.copy(placardWorldPos(p));  // plain move
    else { if (mesh) removePlacardMesh(mesh); buildPlacard(p); }
  });
  placardMeshes.slice().forEach(function (m) {
    if (m.userData.placard && !seen[m.userData.placard.id]) removePlacardMesh(m);
  });
}

placements.forEach(buildPlacard);

// Live sync from the layout editor (BroadcastChannel): reposition unchanged pieces
// in place (no texture reload), rebuild those whose wall/rotation changed, add new
// ones, and remove any that are gone.
function applyPlacements(list) {
  var byId = {};
  artworkMeshes.forEach(function (m) { if (m.userData && m.userData.art) byId[m.userData.art.id] = m; });
  var seen = {};
  list.forEach(function (p) {
    var id = p.artwork.id;
    seen[id] = true;
    var mesh = byId[id];
    var samePlace = mesh && mesh.userData.wall === p.wall && (mesh.userData.rotation || 0) === (p.rotation || 0);
    if (samePlace) {
      mesh.position.copy(artworkWorldPos(p));   // plain move — no texture reload
    } else {
      if (mesh) removeArtworkMesh(mesh);
      buildArtwork(p);
    }
  });
  artworkMeshes.slice().forEach(function (m) {
    if (m.userData && m.userData.art && !seen[m.userData.art.id]) removeArtworkMesh(m);
  });
  syncPlacards(list);
}

// ── Supports (pedestals / shelves): plain boxes ──────────────────────────────
var supportMeshes = [];
var SUPPORT_COLOR = 0xffffff;   // default: white, black outline (see below)

function buildSupport(s) {
  var w = s.w_in * IN2M, h = s.h_in * IN2M, d = s.d_in * IN2M;
  var mat = new THREE.MeshStandardMaterial({ color: SUPPORT_COLOR, roughness: 0.9 });
  if (s.texture) {
    // One texture mapped onto all six faces (single material on the BoxGeometry).
    textureLoader.load(s.texture, function (tex) {
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      mat.map = tex;
      mat.color.set(0xffffff);
      mat.needsUpdate = true;
    }, undefined, function () {
      console.warn('3D viewer: failed to load support texture', s.texture);
    });
  }
  var isFloorCeil = (s.wall === 'floor' || s.wall === 'ceiling');
  var geo, mesh;
  var pos = new THREE.Vector3(s.x_in * IN2M, s.y_in * IN2M, s.z_in * IN2M);
  if (isFloorCeil) {
    geo = new THREE.BoxGeometry(w, h, d);
    mesh = new THREE.Mesh(geo, mat);
    var cy = (s.wall === 'ceiling') ? (H - h / 2) : (s.y_in * IN2M + h / 2);
    mesh.position.set(pos.x, cy, pos.z);
    if (s.rotation) mesh.rotation.y = ((s.rotation % 360) * Math.PI) / 180;
  } else {
    // Back flush with the wall, extending inward by its depth.
    var alongZ = (s.wall === 'E' || s.wall === 'W');   // along-wall dimension runs on Z
    geo = alongZ ? new THREE.BoxGeometry(d, h, w) : new THREE.BoxGeometry(w, h, d);
    mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(pos.clone().addScaledVector(wallNormal(s.wall), d / 2));
  }
  mesh.userData = { support: s };
  scene.add(mesh);
  supportMeshes.push(mesh);
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),
                                  new THREE.MeshBasicMaterial({ color: 0x000000 })));
}

function clearSupports() {
  supportMeshes.forEach(function (m) {
    scene.remove(m);
    m.traverse(function (o) {
      if (o.geometry) o.geometry.dispose();
      var mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      mats.forEach(function (mm) { if (mm.dispose) mm.dispose(); });
    });
  });
  supportMeshes = [];
}
function applySupports(list) {
  clearSupports();
  supportsById = {};
  (list || []).forEach(function (s) { supportsById[s.id] = s; buildSupport(s); });
}

(window.SUPPORTS || []).forEach(buildSupport);

if (window.BroadcastChannel && window.ROOM_SLUG) {
  var _roomChan = new BroadcastChannel('room-layout-' + window.ROOM_SLUG);
  _roomChan.addEventListener('message', function (e) {
    if (!e.data || e.data.type !== 'placements') return;
    if (Array.isArray(e.data.supports)) applySupports(e.data.supports);      // rebuild boxes
    if (Array.isArray(e.data.placements)) applyPlacements(e.data.placements);
  });
}

// ── Obstacle planes ───────────────────────────────────────────────────────────
const obstacleMat = new THREE.MeshBasicMaterial({ color: 0x555555, transparent: true, opacity: 0.55, side: THREE.DoubleSide });

function obstaclePosition(ob) {
  // N/S walls: x_in is horizontal, z_in is irrelevant (fixed at wall face).
  // E/W walls: z_in is horizontal along wall, x_in is irrelevant (fixed at wall face).
  if (ob.wall === 'N') return new THREE.Vector3(ob.x_in * IN2M, ob.y_in * IN2M, -D / 2);
  if (ob.wall === 'S') return new THREE.Vector3(ob.x_in * IN2M, ob.y_in * IN2M,  D / 2);
  if (ob.wall === 'E') return new THREE.Vector3( W / 2, ob.y_in * IN2M, ob.z_in * IN2M);
  if (ob.wall === 'W') return new THREE.Vector3(-W / 2, ob.y_in * IN2M, ob.z_in * IN2M);
  return new THREE.Vector3(ob.x_in * IN2M, ob.wall === 'ceiling' ? H : 0, ob.z_in * IN2M);
}

(cfg.obstacles || []).forEach(function (ob) {
  var ow = ob.w_in * IN2M;
  var oh = ob.h_in * IN2M;
  var geo = new THREE.PlaneGeometry(ow, oh);
  var mesh = new THREE.Mesh(geo, obstacleMat);

  var pos = obstaclePosition(ob);
  var norm = wallNormal(ob.wall);
  pos.addScaledVector(norm, WALL_OFFSET * 0.5);  // slightly behind artworks
  mesh.position.copy(pos);
  mesh.quaternion.copy(wallQuaternion(ob.wall));

  // Thin border
  var borderMat = new THREE.MeshBasicMaterial({ color: 0x222222 });
  var border = new THREE.LineSegments(new THREE.EdgesGeometry(geo), borderMat);
  mesh.add(border);
  scene.add(mesh);
});

// ── Controls (PointerLock) ────────────────────────────────────────────────────
const controls = new PointerLockControls(camera, renderer.domElement);
const crosshair = document.getElementById('crosshair');
const hud = document.getElementById('hud');

// Touch devices (phones/tablets) have no pointer-lock or keyboard, so we use
// drag-to-look plus on-screen buttons instead of mouse-look + WASD.
const TOUCH = window.matchMedia('(hover: none) and (pointer: coarse)').matches;

// Manual look state, used in touch mode (yaw around Y, pitch around X).
const _lookEuler = new THREE.Euler(0, 0, 0, 'YXZ');
const PITCH_MAX = Math.PI / 2 * 0.95;
let yaw = 0, pitch = 0;
function applyLook() {
  _lookEuler.set(pitch, yaw, 0, 'YXZ');
  camera.quaternion.setFromEuler(_lookEuler);
}

// Movement flags driven by the on-screen buttons. (Looking around is done by
// dragging on the view, so there are no turn buttons.)
const touchMove = { fwd: false, back: false };

if (TOUCH) {
  setupTouchControls();
} else {
  renderer.domElement.addEventListener('click', function () {
    if (!controls.isLocked) { controls.lock(); return; }   // first click enters look mode
    pickAt(0, 0);                                           // then a click acts on the crosshair
  });
  controls.addEventListener('lock', function () {
    crosshair.style.display = 'block';
    hud.style.display = 'none';
  });
  controls.addEventListener('unlock', function () {
    crosshair.style.display = 'none';
    hud.style.display = 'block';
  });
}

function setupTouchControls() {
  crosshair.style.display = 'block';
  hud.textContent = 'Drag to look · buttons to walk · double-tap to reset';
  document.getElementById('touch-controls').style.display = 'flex';
  applyLook();

  // One-finger drag rotates the view; a double-tap (quick, stationary) resets it.
  const el = renderer.domElement;
  const LOOK_SENS = 0.005;
  const TAP_MOVE_TOL = 12, TAP_MAX_MS = 300, DOUBLE_TAP_MS = 320;
  let dragging = false, lastX = 0, lastY = 0;
  let tapStartX = 0, tapStartY = 0, tapStartT = 0, moved = false, lastTapT = 0;

  el.addEventListener('touchstart', function (e) {
    if (e.touches.length !== 1) return;
    dragging = true;
    moved = false;
    lastX = tapStartX = e.touches[0].clientX;
    lastY = tapStartY = e.touches[0].clientY;
    tapStartT = performance.now();
  }, { passive: true });
  el.addEventListener('touchmove', function (e) {
    if (!dragging || e.touches.length !== 1) return;
    e.preventDefault();
    const t = e.touches[0];
    yaw   -= (t.clientX - lastX) * LOOK_SENS;
    pitch -= (t.clientY - lastY) * LOOK_SENS;
    pitch = Math.max(-PITCH_MAX, Math.min(PITCH_MAX, pitch));
    lastX = t.clientX;
    lastY = t.clientY;
    if (Math.hypot(t.clientX - tapStartX, t.clientY - tapStartY) > TAP_MOVE_TOL) moved = true;
  }, { passive: false });
  el.addEventListener('touchend', function () {
    dragging = false;
    const now = performance.now();
    if (!moved && now - tapStartT < TAP_MAX_MS) {
      // A tap on a piece/placard acts on it; otherwise a second tap resets the view.
      var rect = el.getBoundingClientRect();
      var nx = ((tapStartX - rect.left) / rect.width) * 2 - 1;
      var ny = -(((tapStartY - rect.top) / rect.height) * 2 - 1);
      if (pickAt(nx, ny)) { lastTapT = 0; return; }
      if (now - lastTapT < DOUBLE_TAP_MS) { resetCamera(); lastTapT = 0; }
      else lastTapT = now;
    }
  });
  el.addEventListener('touchcancel', function () { dragging = false; });

  // Press-and-hold walk buttons.
  function hold(id, set) {
    const b = document.getElementById(id);
    const down = function (e) { e.preventDefault(); set(true); };
    const up   = function (e) { e.preventDefault(); set(false); };
    b.addEventListener('touchstart', down, { passive: false });
    b.addEventListener('touchend', up);
    b.addEventListener('touchcancel', up);
    b.addEventListener('mousedown', down);
    b.addEventListener('mouseup', up);
    b.addEventListener('mouseleave', up);
  }
  hold('tc-fwd',   function (v) { touchMove.fwd = v; });
  hold('tc-back',  function (v) { touchMove.back = v; });
}

// ── Movement ──────────────────────────────────────────────────────────────────
const keys = {};
let pendingLock = false;   // set when returning from a detail page; lock on first key
document.addEventListener('keydown', function (e) {
  keys[e.code] = true;
  if (pendingLock && !controls.isLocked) { pendingLock = false; relock(); }
  if (e.code === 'KeyR' && controls.isLocked) resetCamera();
});
document.addEventListener('keyup',   function (e) { keys[e.code] = false; });

function resetCamera() {
  camera.position.set(0, EYE_H, 0);
  if (TOUCH) {
    yaw = 0; pitch = 0;
    applyLook();
  } else {
    camera.quaternion.set(0, 0, 0, 1);  // identity — looking down -Z (north)
  }
}

const SPEED      = 60 * IN2M;       // 60 in/s walking speed
const TURN_SPEED = Math.PI * 0.2;   // ~36 °/s turn speed (Q / E keys)
const MARGIN     = 0.3;             // metres from wall

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

let lastTime = null;

// ── Raycaster (click picking + crosshair hover cue) ───────────────────────────
const raycaster = new THREE.Raycaster();
let hoverAccum = 0;               // seconds since last hover raycast (throttled)
const HOVER_INTERVAL = 0.08;      // ~12×/s is plenty for a crosshair cue
let crosshairHot = false;

// Reusable scratch objects — avoid per-frame allocation (GC pressure → stutter)
const _vel      = new THREE.Vector3();
const _turnAxis = new THREE.Vector3(0, 1, 0);
const _turnQ    = new THREE.Quaternion();
const _center   = new THREE.Vector2(0, 0);

// ── Click interactions: open a piece's detail page, or enlarge a placard ───────
const _ndc = new THREE.Vector2();
const placardFS = document.getElementById('placard-fullscreen');

function openArtwork(art) {
  if (!art || !window.ARTWORK_URL) return;
  saveCameraState();                                   // so the browser Back button returns here
  window.location.href = window.ARTWORK_URL.replace(/0\/?$/, art.id + '/');
}

function openPlacardFullscreen(art) {
  if (!art || !placardFS) return;
  if (controls.isLocked) controls.unlock();            // free the cursor for the overlay
  var meta = [];
  if (art.year)   meta.push(esc(String(art.year)));
  if (art.medium) meta.push(esc(art.medium));
  if (art.dims)   meta.push(esc(art.dims));
  var card = placardFS.querySelector('.pfs-card');
  card.innerHTML =
    '<span class="pfs-close">×</span>' +
    '<div class="pfs-name">' + esc(art.name || '') + '</div>' +
    (art.artists ? '<div class="pfs-artist">' + esc(art.artists) + '</div>' : '') +
    (meta.length ? '<div class="pfs-meta">' + meta.join(' · ') + '</div>' : '') +
    (art.price ? '<div class="pfs-price">' + esc(art.price) + (art.sold ? ' — sold' : '') + '</div>' : '');
  placardFS.style.display = 'flex';
}
function closePlacardFullscreen() {
  if (placardFS) placardFS.style.display = 'none';
  relock();   // stay in navigation mode — no extra click needed
}
// Re-enter pointer-lock navigation. Works from a user gesture (e.g. closing the
// placard); on a page Back it's best-effort since browsers require activation.
function relock() {
  if (TOUCH || controls.isLocked) return;
  try { controls.lock(); } catch (e) {}
}
if (placardFS) {
  placardFS.addEventListener('click', function (e) {
    // Click the backdrop or the × to close; clicks inside the card do nothing.
    if (e.target === placardFS || e.target.classList.contains('pfs-close')) closePlacardFullscreen();
  });
  // Dismiss the enlarged placard with Return (or Escape).
  document.addEventListener('keydown', function (e) {
    if (placardFS.style.display === 'none') return;
    if (e.code === 'Enter' || e.code === 'NumpadEnter' || e.code === 'Escape') {
      e.preventDefault();
      closePlacardFullscreen();
    }
  });
}

// Raycast at NDC coords; a placard wins over a piece behind it. Returns true if
// something was picked.
function pickAt(x, y) {
  _ndc.set(x, y);
  raycaster.setFromCamera(_ndc, camera);
  var pl = raycaster.intersectObjects(placardMeshes, false);
  var ar = raycaster.intersectObjects(artworkMeshes, false);
  var plHit = pl.length ? pl[0] : null;
  var arHit = ar.length ? ar[0] : null;
  if (plHit && (!arHit || plHit.distance <= arHit.distance)) { openPlacardFullscreen(plHit.object.userData.placard); return true; }
  if (arHit) { openArtwork(arHit.object.userData.art); return true; }
  return false;
}

// Persist / restore the camera so leaving for a detail page and coming Back lands
// exactly where you left off (cleared once consumed, so a fresh visit is default).
const CAM_KEY = 'room3d-cam-' + (window.ROOM_SLUG || '');
function saveCameraState() {
  try {
    sessionStorage.setItem(CAM_KEY, JSON.stringify({
      p: camera.position.toArray(), q: camera.quaternion.toArray(),
      yaw: yaw, pitch: pitch, touch: TOUCH,
    }));
  } catch (e) {}
}
function restoreCameraState() {
  try {
    var raw = sessionStorage.getItem(CAM_KEY);
    if (!raw) return;
    sessionStorage.removeItem(CAM_KEY);
    var s = JSON.parse(raw);
    if (s.p) camera.position.fromArray(s.p);
    if (TOUCH) { yaw = s.yaw || 0; pitch = s.pitch || 0; applyLook(); }
    else if (s.q) camera.quaternion.fromArray(s.q);
    relock();          // try to resume navigation without a click…
    pendingLock = true; // …and if the browser blocked it, lock on the first key press
  } catch (e) {}
}
// If the browser restores this page from the back/forward cache, the live camera
// is already intact — just drop the saved state so a later fresh visit is default.
window.addEventListener('pageshow', function (e) {
  if (e.persisted) { try { sessionStorage.removeItem(CAM_KEY); } catch (err) {} }
});

// ── Animate ───────────────────────────────────────────────────────────────────
function animate(now) {
  requestAnimationFrame(animate);

  if (lastTime === null) lastTime = now;
  var dt = Math.min((now - lastTime) / 1000, 0.1);
  lastTime = now;

  if (controls.isLocked || TOUCH) {
    // Touch: apply the drag-look orientation before moving so we walk where we face.
    if (TOUCH) applyLook();

    _vel.set(0, 0, 0);
    if (keys['KeyW'] || keys['ArrowUp']    || touchMove.fwd)  _vel.z -= 1;
    if (keys['KeyS'] || keys['ArrowDown']  || touchMove.back) _vel.z += 1;
    if (keys['KeyA'] || keys['ArrowLeft'])  _vel.x -= 1;
    if (keys['KeyD'] || keys['ArrowRight']) _vel.x += 1;
    if (_vel.x || _vel.z) {
      _vel.normalize().multiplyScalar(SPEED * dt);
      controls.moveRight(_vel.x);
      controls.moveForward(-_vel.z);
    }

    // Q / E — rotate left / right around world Y (desktop keyboard only)
    if (!TOUCH && (keys['KeyQ'] || keys['KeyE'])) {
      var turnDir = keys['KeyQ'] ? 1 : -1;
      _turnQ.setFromAxisAngle(_turnAxis, turnDir * TURN_SPEED * dt);
      camera.quaternion.premultiply(_turnQ);
    }

    // Clamp to room bounds
    camera.position.x = clamp(camera.position.x, -HW + MARGIN, HW - MARGIN);
    camera.position.z = clamp(camera.position.z, -HD + MARGIN, HD - MARGIN);
    camera.position.y = EYE_H;  // no vertical movement / gravity

    // No hover descriptions — but light up the crosshair when it's over a piece
    // or placard, so it's clear you can click. (Throttled.)
    hoverAccum += dt;
    if (hoverAccum >= HOVER_INTERVAL) {
      hoverAccum = 0;
      raycaster.setFromCamera(_center, camera);
      var over = raycaster.intersectObjects(placardMeshes, false).length > 0 ||
                 raycaster.intersectObjects(artworkMeshes, false).length > 0;
      if (over !== crosshairHot) {
        crosshairHot = over;
        crosshair.classList.toggle('hot', over);
      }
    }
  }

  renderer.render(scene, camera);
}

// ── Resize handling ───────────────────────────────────────────────────────────
const ro = new ResizeObserver(setSize);
ro.observe(canvas.parentElement);
setSize();

restoreCameraState();   // return-to-exact-view after visiting an artwork's detail page
requestAnimationFrame(animate);
