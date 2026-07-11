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
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.shadowMap.enabled = true;
function setSize() {
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  camera.aspect = canvas.clientWidth / canvas.clientHeight;
  camera.updateProjectionMatrix();
}

// ── Scene ─────────────────────────────────────────────────────────────────────
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a1a);
scene.fog = new THREE.Fog(0x1a1a1a, 5, 30);

// ── Camera ────────────────────────────────────────────────────────────────────
const camera = new THREE.PerspectiveCamera(75, 1, 0.01, 100);
const EYE_H = 60 * IN2M;   // 60" eye height
camera.position.set(0, EYE_H, 0);

// ── Lighting ──────────────────────────────────────────────────────────────────
scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const sun = new THREE.DirectionalLight(0xffffff, 0.8);
sun.position.set(0, H * 0.9, 0);
scene.add(sun);

// Small warm fill lights near ceiling
[[HW * 0.4, H * 0.95, 0], [-HW * 0.4, H * 0.95, 0],
 [0, H * 0.95, HD * 0.4], [0, H * 0.95, -HD * 0.4]].forEach(function ([x, y, z]) {
  var pt = new THREE.PointLight(0xfff5e0, 0.4, 20);
  pt.position.set(x, y, z);
  scene.add(pt);
});

// ── Room geometry ─────────────────────────────────────────────────────────────
const roomMat = {
  walls:   new THREE.MeshLambertMaterial({ color: 0xf0ece4, side: THREE.BackSide }),
  floor:   new THREE.MeshLambertMaterial({ color: 0xb8a890, side: THREE.BackSide }),
  ceiling: new THREE.MeshLambertMaterial({ color: 0xfaf8f4, side: THREE.BackSide }),
};

// Build room as 6 separate planes (back-face visible from inside)
function addPlane(w, h, mat, posX, posY, posZ, rotX, rotY) {
  const geo = new THREE.PlaneGeometry(w, h);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(posX, posY, posZ);
  mesh.rotation.set(rotX, rotY, 0);
  scene.add(mesh);
}

// Floor / ceiling
addPlane(W, D, roomMat.floor,   0, 0,  0,  -Math.PI / 2, 0);
addPlane(W, D, roomMat.ceiling, 0, H,  0,   Math.PI / 2, 0);
// North wall (z = -HD, faces south)
addPlane(W, H, roomMat.walls,   0, H/2, -HD, 0, 0);
// South wall (z = +HD, faces north)
addPlane(W, H, roomMat.walls,   0, H/2,  HD, 0, Math.PI);
// East wall  (x = +HW, faces west)
addPlane(D, H, roomMat.walls,   HW, H/2, 0, 0, -Math.PI / 2);
// West wall  (x = -HW, faces east)
addPlane(D, H, roomMat.walls,  -HW, H/2, 0, 0,  Math.PI / 2);

// ── Artwork planes ────────────────────────────────────────────────────────────
const textureLoader = new THREE.TextureLoader();
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
  // Orient the plane so it faces inward
  const q = new THREE.Quaternion();
  if (wall === 'N') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0);
  else if (wall === 'S') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
  else if (wall === 'E') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
  else if (wall === 'W') q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -Math.PI / 2);
  else if (wall === 'ceiling') q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
  else q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
  return q;
}

// Placard data for hover
const artworkMeshes = [];

placements.forEach(function (p) {
  var art = p.artwork;
  var aw = art.w_in * IN2M;
  var ah = art.h_in * IN2M;
  var geo = new THREE.PlaneGeometry(aw, ah);

  var mat = new THREE.MeshBasicMaterial({ color: 0x999999, side: THREE.FrontSide });
  var mesh = new THREE.Mesh(geo, mat);

  var pos = placementPosition(p);
  var norm = wallNormal(p.wall);
  // Push slightly off the wall surface
  pos.addScaledVector(norm, WALL_OFFSET);
  mesh.position.copy(pos);
  mesh.quaternion.copy(wallQuaternion(p.wall));

  // Load texture
  if (art.img) {
    textureLoader.load(art.img, function (tex) {
      tex.colorSpace = THREE.SRGBColorSpace;
      mesh.material = new THREE.MeshBasicMaterial({ map: tex, side: THREE.FrontSide });
    });
  }

  mesh.userData = { art: art, wall: p.wall };
  scene.add(mesh);
  artworkMeshes.push(mesh);

  // Frame (thin border)
  var frameMat = new THREE.MeshBasicMaterial({ color: 0x333333 });
  var frameGeo = new THREE.EdgesGeometry(geo);
  var frame = new THREE.LineSegments(frameGeo, frameMat);
  mesh.add(frame);
});

// ── Controls (PointerLock) ────────────────────────────────────────────────────
const controls = new PointerLockControls(camera, renderer.domElement);
const crosshair = document.getElementById('crosshair');
const hud = document.getElementById('hud');

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

// ── Movement ──────────────────────────────────────────────────────────────────
const keys = {};
document.addEventListener('keydown', function (e) { keys[e.code] = true; });
document.addEventListener('keyup',   function (e) { keys[e.code] = false; });

const SPEED = 60 * IN2M;   // 60 in/s walking speed
const MARGIN = 0.3;         // metres from wall

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

let lastTime = null;

// ── Raycaster for placard overlay ─────────────────────────────────────────────
const raycaster = new THREE.Raycaster();
const placardOverlay = document.getElementById('placard-overlay');
let lastHovered = null;

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

  if (controls.isLocked) {
    var vel = new THREE.Vector3();
    if (keys['KeyW'] || keys['ArrowUp'])    vel.z -= 1;
    if (keys['KeyS'] || keys['ArrowDown'])  vel.z += 1;
    if (keys['KeyA'] || keys['ArrowLeft'])  vel.x -= 1;
    if (keys['KeyD'] || keys['ArrowRight']) vel.x += 1;
    vel.normalize().multiplyScalar(SPEED * dt);
    controls.moveRight(vel.x);
    controls.moveForward(-vel.z);

    // Clamp to room bounds
    camera.position.x = clamp(camera.position.x, -HW + MARGIN, HW - MARGIN);
    camera.position.z = clamp(camera.position.z, -HD + MARGIN, HD - MARGIN);
    camera.position.y = EYE_H;  // no vertical movement / gravity

    // Raycast from centre of screen
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    var hits = raycaster.intersectObjects(artworkMeshes);
    if (hits.length && hits[0].distance < 3) {
      var rect = canvas.getBoundingClientRect();
      updatePlacard(hits[0].object.userData.art, rect.width / 2, rect.height / 2);
    } else {
      updatePlacard(null);
    }
  }

  renderer.render(scene, camera);
}

// ── Resize handling ───────────────────────────────────────────────────────────
const ro = new ResizeObserver(setSize);
ro.observe(canvas.parentElement);
setSize();

requestAnimationFrame(animate);
