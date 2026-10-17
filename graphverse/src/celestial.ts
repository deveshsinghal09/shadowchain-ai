import * as THREE from 'three';
import type { Entity } from '../shared/types';
import { colors } from './store';
import { random } from './cosmos';
export const anchors: Record<string, [number, number, number]> = {
  infrastructure: [35, 15, 25],
  identity: [-210, 90, 0],
  threat: [160, 155, -60],
  memory: [-150, -140, 30],
  incident: [230, -110, 0],
};
const sphere = new THREE.SphereGeometry(1, 32, 24),
  lowSphere = new THREE.SphereGeometry(1, 12, 8),
  fragment = new THREE.IcosahedronGeometry(1, 1);
function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d')!,
    g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, 'rgba(255,255,255,0.95)');
  g.addColorStop(0.12, 'rgba(255,255,255,0.6)');
  g.addColorStop(0.35, 'rgba(255,255,255,0.14)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}
let texture: THREE.Texture | undefined;
const surfaceVertex = `varying vec3 vNormal;varying vec3 vPosition;varying vec3 vWorld; void main(){vNormal=normalize(normalMatrix*normal);vPosition=position;vWorld=(modelViewMatrix*vec4(position,1.)).xyz;gl_Position=projectionMatrix*vec4(vWorld,1.);}`;
const surfaceFragment = `varying vec3 vNormal;varying vec3 vPosition;varying vec3 vWorld;uniform vec3 tint;uniform float time;uniform float kind;uniform float intensity;
  float hash(vec3 p){return fract(sin(dot(p,vec3(12.9898,78.233,45.164)))*43758.5453);}
  float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
  void main(){float n=noise(vPosition*5.+vec3(time*.035,0,0))*.6+noise(vPosition*17.)*.3+noise(vPosition*39.)*.1;
    float rim=pow(1.-max(dot(normalize(vNormal),normalize(-vWorld)),0.),2.6);
    float light=max(dot(normalize(vNormal),normalize(vec3(-.8,1.,1.))),0.);
    vec3 base=tint*(.14+light*.55+n*.36);if(kind>1.5)base=tint*(.2+pow(n,2.)*2.4);
    if(kind<.5)base=mix(vec3(.025,.035,.05),tint*.6,step(.52,n))*(.2+light);
    gl_FragColor=vec4((base+tint*rim*1.55)*intensity,1.);}`;
export function celestial(node: Entity) {
  texture ??= glowTexture();
  const group = new THREE.Group(),
    color = colors[node.cluster] ?? '#c4a6ff';
  const cluster = node.type === 'Cluster',
    important = (node.degree ?? 0) > 4;
  const radius = cluster
    ? 3.6
    : node.id === 'atlas'
      ? 9
      : node.id === 'vault'
        ? 7
        : node.type === 'Incident'
          ? 6
          : node.type === 'Resource'
            ? 3.8
            : 2.3 + Math.min(node.degree ?? 0, 12) * 0.23;
  const uniforms = {
    tint: { value: new THREE.Color(node.type === 'Incident' ? '#ff637f' : color) },
    time: { value: 0 },
    kind: { value: node.type === 'Resource' ? 0 : node.type === 'Identity' ? 1 : 2 },
    intensity: { value: 1 },
  };
  const body = new THREE.Mesh(
    node.type === 'Vulnerability' ? fragment : sphere,
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader: surfaceVertex,
      fragmentShader: surfaceFragment,
    }),
  );
  body.scale.setScalar(radius);
  group.add(body);
  const halo = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: texture,
      color,
      transparent: true,
      opacity: cluster ? 0.45 : 0.36,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  halo.scale.setScalar(radius * 7);
  group.add(halo);
  if (node.type === 'ThreatActor' || node.id === 'vault') {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(radius * 1.5, radius * 2.6, 80),
      new THREE.MeshBasicMaterial({
        color: node.type === 'ThreatActor' ? '#f66d73' : color,
        transparent: true,
        opacity: 0.25,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    ring.rotation.x = 1.15;
    ring.rotation.y = 0.25;
    group.add(ring);
  }
  if (cluster) {
    const rand = random(node.id.length * 97),
      pts = [],
      cs = [];
    for (let i = 0; i < 850; i++) {
      const r = Math.pow(rand(), 0.7) * 33,
        a = r * 0.16 + ((i % 3) * Math.PI * 2) / 3 + (rand() - 0.5) * 1.35;
      pts.push(Math.cos(a) * r, (rand() - 0.5) * r * 0.12, Math.sin(a) * r);
      const c = new THREE.Color(color).lerp(new THREE.Color('white'), 1 - r / 33);
      cs.push(c.r, c.g, c.b);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(cs, 3));
    const galaxy = new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        size: 1.6,
        map: texture,
        vertexColors: true,
        transparent: true,
        opacity: 0.58,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    galaxy.rotation.x = 0.7;
    group.add(galaxy);
    group.userData.galaxy = galaxy;
  }
  if (
    cluster ||
    important ||
    [
      'alice',
      'vault',
      'ghost',
      'memo-access',
      'memo-deny',
      'incident-31',
      'reader-role',
      'vega',
    ].includes(node.id)
  ) {
    const c = document.createElement('canvas');
    c.width = 512;
    c.height = 80;
    const ctx = c.getContext('2d')!;
    ctx.font = `${cluster ? '500 24' : '400 24'}px 'Instrument Sans',sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillStyle = cluster ? '#a1aabe' : '#dce7f8';
    ctx.fillText(node.label, 256, 32);
    if (cluster) {
      ctx.font = "16px 'IBM Plex Mono',monospace";
      ctx.fillStyle = '#65748d';
      ctx.fillText('CURATED REGION', 256, 59);
    }
    const label = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(c),
        transparent: true,
        depthTest: false,
        sizeAttenuation: false,
        opacity: cluster ? 0.75 : 0.9,
      }),
    );
    label.scale.set(0.22, 0.0344, 1);
    label.position.set(0, cluster ? -28 : -radius - 11, 0);
    group.add(label);
    group.userData.label = label;
  }
  group.userData = { ...group.userData, node, uniforms, body, halo };
  return group;
}
export function disposeObject(object: THREE.Object3D) {
  object.traverse((o) => {
    const obj = o as THREE.Mesh;
    if (
      obj.geometry &&
      obj.geometry !== sphere &&
      obj.geometry !== lowSphere &&
      obj.geometry !== fragment
    )
      obj.geometry.dispose();
    const materials = obj.material
      ? Array.isArray(obj.material)
        ? obj.material
        : [obj.material]
      : [];
    for (const m of materials) {
      const map = (m as THREE.SpriteMaterial).map;
      if (map && map !== texture) map.dispose();
      m.dispose();
    }
  });
}
export function updateLOD(object: THREE.Group, distance: number, performance: boolean) {
  const body = object.userData.body as THREE.Mesh;
  if (body.geometry === sphere || body.geometry === lowSphere)
    body.geometry = performance || distance > 500 ? lowSphere : sphere;
}
