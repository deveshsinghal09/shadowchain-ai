import { memo, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph3D, { type ForceGraphMethods, type LinkObject } from 'react-force-graph-3d';
import { createRoot, extend, type RootState } from '@react-three/fiber';
import * as THREE from 'three';
import { Pass } from 'three/addons/postprocessing/Pass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { gsap } from 'gsap';
import type { Edge, Entity } from '../shared/types';
import { useExplorer, colors } from './store';
import { Cosmos, random } from './cosmos';
import { anchors, celestial, disposeObject, updateLOD } from './celestial';
extend({
  Group: THREE.Group,
  Points: THREE.Points,
  ShaderMaterial: THREE.ShaderMaterial,
  Mesh: THREE.Mesh,
  PlaneGeometry: THREE.PlaneGeometry,
  InstancedMesh: THREE.InstancedMesh,
  IcosahedronGeometry: THREE.IcosahedronGeometry,
  MeshBasicMaterial: THREE.MeshBasicMaterial,
});
type SceneLink = Omit<Edge, 'source' | 'target'>;
type GraphMethods = ForceGraphMethods<Entity, SceneLink>;
class FramePass extends Pass {
  needsSwap = false;
  constructor(private tick: () => void) {
    super();
  }
  render() {
    this.tick();
  }
}
function Universe() {
  const graph = useExplorer((s) => s.graph),
    hidden = useExplorer((s) => s.hiddenClusters),
    quality = useExplorer((s) => s.quality),
    motion = useExplorer((s) => s.motion),
    bloom = useExplorer((s) => s.bloom),
    direction = useExplorer((s) => s.direction),
    path = useExplorer((s) => s.path),
    selected = useExplorer((s) => s.selected),
    action = useExplorer((s) => s.cameraAction),
    pulse = useExplorer((s) => s.pulse),
    journeySession = useExplorer((s) => s.journeySession);
  const fg = useRef<GraphMethods | undefined>(undefined),
    host = useRef<HTMLDivElement>(null),
    objects = useRef(new Map<string, THREE.Group>()),
    root = useRef<ReturnType<typeof createRoot> | null>(null),
    fiberState = useRef<RootState | null>(null),
    bloomPass = useRef<UnrealBloomPass | null>(null),
    flight = useRef<gsap.core.Timeline | null>(null),
    flightStart = useRef(0),
    frame = useRef(0),
    journeyTime = useRef(0),
    lastFrame = useRef(0),
    introStart = useRef(0);
  const [size, setSize] = useState({ width: innerWidth, height: innerHeight }),
    [ready, setReady] = useState(false);
  const backgroundClick = useRef(0);
  const layoutNodes = useRef(new Map<string, Entity>());
  const data = useMemo(() => {
    const rand = random(42);
    const nodes = (graph?.nodes ?? [])
      .filter((n) => !hidden.includes(n.cluster))
      .map((n) => {
        const existing = layoutNodes.current.get(n.id);
        if (existing) {
          Object.assign(existing, n);
          return existing;
        }
        const a = anchors[n.cluster] ?? [0, 0, 0],
          angle = rand() * Math.PI * 2,
          r = n.type === 'Cluster' ? 0 : 24 + rand() * 69;
        const positioned = {
          ...n,
          x: a[0] + Math.cos(angle) * r,
          y: a[1] + Math.sin(angle) * r * 0.7,
          z: a[2] + (rand() - 0.5) * 100,
        };
        layoutNodes.current.set(n.id, positioned);
        return positioned;
      });
    const ids = new Set(nodes.map((n) => n.id));
    return {
      nodes,
      links: (graph?.edges ?? [])
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ ...e })),
    };
  }, [graph, hidden]);
  const pathEdges = useMemo(() => new Set(path?.ordered_edge_ids ?? []), [path]);
  const related = useMemo(
    () =>
      new Set(
        graph?.edges
          .filter((e) => e.source === selected?.id || e.target === selected?.id)
          .map((e) => e.id) ?? [],
      ),
    [graph, selected],
  );
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    const renderer = g.renderer(),
      scene = g.scene(),
      camera = g.camera() as THREE.PerspectiveCamera;
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    camera.far = 6000;
    camera.updateProjectionMatrix();
    g.cameraPosition({ x: 0, y: 15, z: 720 }, { x: 0, y: 0, z: 0 }, 0);
    const control = g.controls() as {
      enableDamping: boolean;
      dampingFactor: number;
      minDistance: number;
      maxDistance: number;
      autoRotate: boolean;
    };
    Object.assign(control, {
      enableDamping: true,
      dampingFactor: 0.075,
      minDistance: 12,
      maxDistance: 2200,
      autoRotate: false,
    });
    let active = true;
    const r = createRoot(renderer.domElement);
    root.current = r;
    void r
      .configure({
        gl: renderer,
        camera,
        scene,
        frameloop: 'never',
        size: { width: innerWidth, height: innerHeight, top: 0, left: 0 },
        dpr: Math.min(devicePixelRatio, 1.5),
        onCreated: (s) => {
          fiberState.current = s;
        },
      })
      .then(() => {
        if (active) {
          r.render(
            <Cosmos
              quality={useExplorer.getState().quality}
              motion={useExplorer.getState().motion}
            />,
          );
          setReady(true);
        }
      });
    const composer = g.postProcessingComposer(),
      pass = new FramePass(() => {
        const now = performance.now() / 1000,
          dt = Math.min(now - (lastFrame.current || now), 0.05);
        lastFrame.current = now;
        fiberState.current?.advance(now, true);
        const state = useExplorer.getState();
        if (flight.current) flight.current.time(now - flightStart.current);
        if (state.journey === 'playing' && state.path?.found) {
          journeyTime.current += dt;
          const step = Math.min(
            Math.floor(journeyTime.current / 3.4),
            state.path.ordered_node_ids.length - 1,
          );
          if (step !== state.step) state.set({ step });
          const id = state.path.ordered_node_ids[step],
            n = dataRef.current.nodes.find((n) => n.id === id);
          if (n) {
            const target = new THREE.Vector3(n.x ?? 0, n.y ?? 0, n.z ?? 0),
              pos = target.clone().add(new THREE.Vector3(25, 18, 95));
            camera.position.lerp(pos, 1 - Math.exp(-dt * 1.9));
            const c = g.controls() as { target: THREE.Vector3 };
            c.target.lerp(target, 1 - Math.exp(-dt * 1.9));
          }
          if (journeyTime.current > state.path.ordered_node_ids.length * 3.4)
            state.set({ journey: 'finished' });
        }
        if (state.intro && now - introStart.current > 10) state.set({ intro: false });
        const pathIds = new Set(state.path?.ordered_node_ids ?? []),
          nearIds = new Set(
            state.graph?.edges
              .filter((e) => e.source === state.selected?.id || e.target === state.selected?.id)
              .flatMap((e) => [e.source, e.target]) ?? [],
          );
        objects.current.forEach((obj, id) => {
          const d = obj.userData;
          updateLOD(obj, camera.position.distanceTo(obj.position), state.quality === 'performance');
          d.uniforms.time.value = state.motion ? now : 0;
          const prominent =
            !state.selected || nearIds.has(id) || id === state.selected.id || pathIds.has(id);
          d.uniforms.intensity.value = prominent ? 1 : 0.3;
          if (d.label) d.label.visible = state.labels && (prominent || d.node.type === 'Cluster');
          if (state.motion) {
            d.body.rotation.y = now * 0.07;
            if (d.galaxy) d.galaxy.rotation.y = now * 0.025;
          }
          d.halo.material.opacity =
            id === state.selected?.id
              ? 0.55 + (state.motion ? Math.sin(now * 2) * 0.12 : 0)
              : prominent
                ? 0.26
                : 0.08;
        });
        frame.current++;
        // Adaptive downward quality adjustment only; avoids oscillating DPR.
        if (frame.current % 240 === 0 && dt > 0.035 && renderer.getPixelRatio() > 1)
          renderer.setPixelRatio(1);
      });
    composer.insertPass(pass, 0);
    const glow = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.5, 0.65, 0.86);
    composer.addPass(glow);
    bloomPass.current = glow;
    const visibility = () => (document.hidden ? g.pauseAnimation() : g.resumeAnimation());
    document.addEventListener('visibilitychange', visibility);
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', visibility);
      flight.current?.kill();
      composer.removePass(pass);
      composer.removePass(glow);
      glow.dispose();
      r.unmount();
      objects.current.forEach(disposeObject);
      objects.current.clear();
    };
  }, []);
  const dataRef = useRef(data);
  dataRef.current = data;
  useEffect(() => {
    if (!ready) return;
    root.current?.render(<Cosmos quality={quality} motion={motion} />);
    fg.current
      ?.renderer()
      .setPixelRatio(
        Math.min(devicePixelRatio, quality === 'cinematic' ? 2 : quality === 'balanced' ? 1.5 : 1),
      );
    if (bloomPass.current) {
      bloomPass.current.enabled = bloom && quality !== 'performance';
      bloomPass.current.strength = quality === 'cinematic' ? 0.65 : 0.4;
    }
  }, [quality, motion, bloom, ready]);
  useEffect(() => {
    if (fiberState.current) fiberState.current.setSize(size.width, size.height);
    bloomPass.current?.setSize(size.width, size.height);
  }, [size]);
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    g.d3Force('charge')?.strength(-32);
    g.d3Force('link')
      ?.distance((link: LinkObject<Entity, SceneLink>) => (link.type === 'MEMBER_OF' ? 55 : 85))
      .strength(0.12);
    g.d3Force(
      'regions',
      Object.assign((alpha: number) => {
        for (const n of data.nodes) {
          const node = n as Entity & { vx: number; vy: number; vz: number };
          const a = anchors[node.cluster] ?? [0, 0, 0];
          node.vx = (node.vx ?? 0) + (a[0] - node.x!) * alpha * 0.035;
          node.vy = (node.vy ?? 0) + (a[1] - node.y!) * alpha * 0.035;
          node.vz = (node.vz ?? 0) + (a[2] - node.z!) * alpha * 0.035;
        }
      }),
    );
  }, [data]);
  useEffect(() => {
    const g = fg.current;
    if (!g || !ready) return;
    flight.current?.kill();
    const state = useExplorer.getState();
    if (action.kind !== 'intro')
      state.set({ intro: false, journey: state.journey === 'playing' ? 'paused' : state.journey });
    let target = new THREE.Vector3(0, 0, 0),
      distance = 720;
    if (action.kind === 'focus') {
      const n = data.nodes.find((n) => n.id === action.id);
      if (n) {
        target.set(n.x ?? 0, n.y ?? 0, n.z ?? 0);
        distance = 160;
      }
    }
    if (action.kind === 'cluster') {
      const ns = data.nodes.filter((n) => n.cluster === action.id);
      if (ns.length) {
        for (const n of ns) target.add(new THREE.Vector3(n.x, n.y, n.z));
        target.divideScalar(ns.length);
        distance = 300;
      }
    }
    if (action.kind === 'intro') {
      introStart.current = performance.now() / 1000;
      g.cameraPosition({ x: -160, y: 150, z: 1550 });
      state.set({ intro: true });
    }
    const c = g.controls() as { target: THREE.Vector3 };
    if (!state.motion) {
      g.cameraPosition({ x: target.x, y: target.y + 15, z: target.z + distance }, target, 0);
      state.set({ intro: false });
      return;
    }
    flightStart.current = performance.now() / 1000;
    flight.current = gsap
      .timeline({ paused: true })
      .to(
        g.camera().position,
        {
          x: target.x,
          y: target.y + 15,
          z: target.z + distance,
          duration: action.kind === 'intro' ? 10 : 1.5,
          ease: 'power2.inOut',
        },
        0,
      )
      .to(
        c.target,
        {
          x: target.x,
          y: target.y,
          z: target.z,
          duration: action.kind === 'intro' ? 10 : 1.5,
          ease: 'power2.inOut',
        },
        0,
      );
  }, [action, ready]);
  const journey = useExplorer((s) => s.journey);
  useEffect(() => {
    journeyTime.current = 0;
  }, [journeySession]);
  useEffect(() => {
    if (motion && fg.current) {
      const ids = new Set(pulse.edgeIds);
      data.links
        .filter((l) => ids.has(l.id))
        .slice(0, 100)
        .forEach((l) => fg.current!.emitParticle(l));
    }
  }, [pulse, motion]);
  useEffect(() => {
    if (journey === 'idle') journeyTime.current = 0;
    if (journey === 'playing') flight.current?.kill();
  }, [journey]);
  const buildObject = (node: Entity) => {
    let obj = objects.current.get(node.id);
    if (!obj) {
      obj = celestial(node);
      objects.current.set(node.id, obj);
    }
    return obj;
  };
  return (
    <div ref={host} className="universe" aria-label="Interactive three-dimensional knowledge graph">
      <ForceGraph3D
        ref={fg}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#040610"
        controlType="orbit"
        showNavInfo={false}
        nodeThreeObject={buildObject}
        nodeLabel={() => ''}
        linkLabel={() => ''}
        nodeVal={(n) => (n.type === 'Cluster' ? 8 : 3)}
        onNodeClick={(n) => useExplorer.getState().focus(n)}
        onBackgroundClick={() => {
          const now = performance.now();
          useExplorer.getState().set({
            selected: null,
            panel: null,
            ...(now - backgroundClick.current < 350
              ? {
                  cameraAction: { kind: 'overview' as const, nonce: Date.now() },
                  journey: 'idle' as const,
                }
              : {}),
          });
          backgroundClick.current = now;
        }}
        onNodeRightClick={(n) => useExplorer.getState().focus(n)}
        linkColor={(l) =>
          pathEdges.has(l.id)
            ? '#c1f3ff'
            : related.has(l.id)
              ? '#8dceff'
              : l.type === 'MEMBER_OF'
                ? '#50618728'
                : `${colors[typeof l.source === 'object' ? l.source.cluster : 'infrastructure'] ?? '#829cd2'}66`
        }
        linkWidth={(l) => (pathEdges.has(l.id) ? 0.65 : related.has(l.id) ? 0.3 : 0)}
        linkOpacity={0.55}
        linkDirectionalArrowLength={(l) =>
          direction && l.type !== 'MEMBER_OF' ? (pathEdges.has(l.id) ? 3 : 1.4) : 0
        }
        linkDirectionalArrowRelPos={0.82}
        linkDirectionalParticles={(l) =>
          motion && journey !== 'paused' && pathEdges.has(l.id) ? 2 : 0
        }
        linkDirectionalParticleWidth={1.5}
        linkDirectionalParticleSpeed={0.004}
        linkDirectionalParticleColor={() => '#d6faff'}
        warmupTicks={50}
        cooldownTicks={100}
        enableNodeDrag={false}
      />
    </div>
  );
}
export default memo(Universe);
