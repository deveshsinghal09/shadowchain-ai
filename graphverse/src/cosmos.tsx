import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Billboard } from '@react-three/drei';
import * as THREE from 'three';
import type { Quality } from './store';
export function random(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const nebulaVertex = `varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`;
const nebulaFragment = `
  varying vec2 vUv; uniform float time; uniform vec3 tint; uniform float seed;
  float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
  float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),f.x),f.y);}
  float fbm(vec2 p){float a=.5,v=0.;mat2 m=mat2(.8,.6,-.6,.8);for(int i=0;i<5;i++){v+=a*noise(p);p=m*p*2.04+3.1;a*=.5;}return v;}
  void main(){vec2 p=(vUv-.5)*vec2(3.,5.);p.x+=sin(p.y*1.6+seed)*.24;
    float n=fbm(p*3.+seed+vec2(time*.009,0.));float fil=fbm(p*8.+n*4.);
    float shape=exp(-dot(p*vec2(.8,.55),p*vec2(.8,.55))*1.7);
    float mask=smoothstep(0.,.15,vUv.x)*smoothstep(0.,.15,1.-vUv.x)*smoothstep(0.,.15,vUv.y)*smoothstep(0.,.15,1.-vUv.y);
    float a=pow(max(n-.21,0.),2.)*shape*1.25*mask;
    vec3 c=mix(tint*.3,tint*1.6,fil);gl_FragColor=vec4(c,a);}`;
function Nebula({
  position,
  tint,
  seed,
  rotation = 0,
  motion,
}: {
  position: [number, number, number];
  tint: string;
  seed: number;
  rotation?: number;
  motion: boolean;
}) {
  const uniforms = useMemo(
    () => ({ time: { value: 0 }, tint: { value: new THREE.Color(tint) }, seed: { value: seed } }),
    [tint, seed],
  );
  useFrame(({ clock }) => {
    uniforms.time.value = motion ? clock.elapsedTime : 0;
  });
  return (
    <Billboard position={position}>
      <mesh rotation={[0, 0, rotation]}>
        <planeGeometry args={[2500, 1700]} />
        <shaderMaterial
          uniforms={uniforms}
          vertexShader={nebulaVertex}
          fragmentShader={nebulaFragment}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </Billboard>
  );
}
function Stars({ count, motion }: { count: number; motion: boolean }) {
  const uniforms = useMemo(
    () => ({ time: { value: 0 }, motion: { value: motion ? 1 : 0 } }),
    [motion],
  );
  const geometry = useMemo(() => {
    const rand = random(4711),
      g = new THREE.BufferGeometry(),
      positions = [],
      colors = [],
      sizes = [];
    for (let i = 0; i < count; i++) {
      const a = rand() * Math.PI * 2,
        b = Math.acos(2 * rand() - 1),
        r = 650 + rand() * 2600;
      positions.push(r * Math.sin(b) * Math.cos(a), r * Math.sin(b) * Math.sin(a), r * Math.cos(b));
      const c = new THREE.Color().setHSL(
        0.55 + rand() * 0.18,
        0.1 + rand() * 0.35,
        0.55 + rand() * 0.4,
      );
      colors.push(c.r, c.g, c.b);
      sizes.push(rand() > 0.985 ? 4.4 : 0.6 + rand() * 1.65);
    }
    g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    g.setAttribute('size', new THREE.Float32BufferAttribute(sizes, 1));
    return g;
  }, [count]);
  useFrame(({ clock }) => {
    uniforms.time.value = clock.elapsedTime;
  });
  return (
    <points geometry={geometry} frustumCulled={false}>
      <shaderMaterial
        uniforms={uniforms}
        vertexShader={`attribute float size; varying vec3 vColor; uniform float time; uniform float motion; void main(){vColor=color*(.78+.22*sin(time*.45*motion+position.x));vec4 mv=modelViewMatrix*vec4(position,1.);gl_PointSize=size*clamp(1200./-mv.z,.5,2.);gl_Position=projectionMatrix*mv;}`}
        fragmentShader={`varying vec3 vColor;void main(){float d=length(gl_PointCoord-.5)*2.;if(d>1.)discard;gl_FragColor=vec4(vColor,pow(1.-d,2.));}`}
        vertexColors
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
function Dust({ motion }: { motion: boolean }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const matrix = useMemo(() => {
    const rand = random(71),
      o = new THREE.Object3D();
    return Array.from({ length: 220 }, () => {
      o.position.set((rand() - 0.5) * 1700, (rand() - 0.5) * 1100, (rand() - 0.5) * 1200);
      o.scale.setScalar(0.06 + rand() * 0.2);
      o.updateMatrix();
      return o.matrix.clone();
    });
  }, []);
  useFrame(({ clock }) => {
    if (ref.current) {
      if (!ref.current.userData.ready) {
        matrix.forEach((m, i) => ref.current!.setMatrixAt(i, m));
        ref.current.instanceMatrix.needsUpdate = true;
        ref.current.userData.ready = true;
      }
      ref.current.rotation.y = motion ? clock.elapsedTime * 0.0008 : 0;
    }
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, 220]} frustumCulled={false}>
      <icosahedronGeometry args={[1, 0]} />
      <meshBasicMaterial color="#b3bddc" transparent opacity={0.25} />
    </instancedMesh>
  );
}
export function Cosmos({ quality, motion }: { quality: Quality; motion: boolean }) {
  // Priority 1 disables Fiber's own render. The force-graph composer owns rendering.
  useFrame(() => {}, 1);
  return (
    <group>
      <Stars
        count={quality === 'cinematic' ? 12000 : quality === 'balanced' ? 6500 : 2200}
        motion={motion}
      />
      <Nebula position={[-400, 60, -900]} tint="#404baf" seed={2} rotation={-0.5} motion={motion} />
      <Nebula
        position={[450, -120, -1150]}
        tint="#644e9a"
        seed={6}
        rotation={0.5}
        motion={motion}
      />
      {quality !== 'performance' && (
        <>
          <Nebula
            position={[0, 180, -1500]}
            tint="#237b95"
            seed={12}
            rotation={0.3}
            motion={motion}
          />
          <Dust motion={motion} />
        </>
      )}
    </group>
  );
}
