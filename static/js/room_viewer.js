/*
 * 3D Gallery Viewer — Three.js ES module
 * World units = inches.  Origin = room centre, floor at y=0.
 * X east (+), Y up (+), Z south (+).
 */
import * as THREE from 'three';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';

const IN2M = 0.0254;           // 1 inch in metres (Three uses metres by default; we work in inches and convert)

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
  mesh.userData = { art: art, wall: p.wall };
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
    var cy = (p.wall === 'ceiling') ? (H - ah / 2) : (ah / 2);
    box.position.set(base.x, cy, base.z);
    if (p.rotation) box.rotation.y = ((p.rotation % 360) * Math.PI) / 180;   // yaw 0/90/180/270
  } else {
    // Depth extends inward from the wall; back face flush with the wall surface.
    box.position.copy(base.clone().addScaledVector(norm, WALL_OFFSET + ad / 2));
    box.quaternion.copy(wallQuaternion(p.wall));
  }
  box.userData = { art: art, wall: p.wall };
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

placements.forEach(function (p) {
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
});

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
    if (!controls.isLocked) controls.lock();
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
      // Quick stationary tap — a second one right after resets the view.
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
document.addEventListener('keydown', function (e) {
  keys[e.code] = true;
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

// ── Raycaster for placard overlay ─────────────────────────────────────────────
const raycaster = new THREE.Raycaster();
const placardOverlay = document.getElementById('placard-overlay');
let lastHovered = null;
let raycastAccum = 0;              // seconds since last raycast (throttled)
const RAYCAST_INTERVAL = 0.1;     // raycast at most ~10×/s, not every frame

// Reusable scratch objects — avoid per-frame allocation (GC pressure → stutter)
const _vel      = new THREE.Vector3();
const _turnAxis = new THREE.Vector3(0, 1, 0);
const _turnQ    = new THREE.Quaternion();
const _center   = new THREE.Vector2(0, 0);

function updatePlacard(art, screenX, screenY) {
  if (!art) {
    placardOverlay.innerHTML = '';
    lastHovered = null;
    return;
  }
  if (lastHovered === art.id) return;
  lastHovered = art.id;
  var year = art.year ? ' (' + art.year + ')' : '';
  var medium = art.medium ? '<br>' + art.medium : '';
  var dims = art.dims ? '<br>' + art.dims : '';
  placardOverlay.innerHTML =
    '<div style="position:absolute;left:' + (screenX + 12) + 'px;top:' + (screenY - 10) + 'px;' +
    'background:rgba(255,255,255,.92);padding:6px 10px;border-radius:4px;font-size:.75rem;max-width:200px;pointer-events:none">' +
    '<strong>' + art.name + '</strong>' + year + '<br>' + (art.artists || '') + medium + dims +
    '</div>';
}

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

    // Raycast from centre of screen — throttled; non-recursive (meshes' children are frames)
    raycastAccum += dt;
    if (raycastAccum >= RAYCAST_INTERVAL) {
      raycastAccum = 0;
      raycaster.setFromCamera(_center, camera);
      var hits = raycaster.intersectObjects(artworkMeshes, false);
      if (hits.length && hits[0].distance < 3) {
        var rect = canvas.getBoundingClientRect();
        updatePlacard(hits[0].object.userData.art, rect.width / 2, rect.height / 2);
      } else {
        updatePlacard(null);
      }
    }
  }

  renderer.render(scene, camera);
}

// ── Resize handling ───────────────────────────────────────────────────────────
const ro = new ResizeObserver(setSize);
ro.observe(canvas.parentElement);
setSize();

requestAnimationFrame(animate);
