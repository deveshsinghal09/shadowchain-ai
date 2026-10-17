# Third-party foundations

The application uses these open-source projects; their source and licenses remain their authors' work:

- [3d-force-graph](https://github.com/vasturiano/3d-force-graph) and [react-force-graph](https://github.com/vasturiano/react-force-graph): graph layout, picking, camera controls and the main render loop (MIT).
- [Three.js](https://github.com/mrdoob/three.js): rendering, shaders and EffectComposer/UnrealBloomPass (MIT).
- [React Three Fiber](https://github.com/pmndrs/react-three-fiber) and [Drei](https://github.com/pmndrs/drei): shared scene reconciliation and billboards (MIT).
- [FalkorDB TypeScript client](https://github.com/FalkorDB/falkordb-ts): official graph client (MIT).
- React, Vite, Express, Zod, Tailwind CSS, Zustand, Helmet, express-rate-limit, Lucide and Fontsource: application and interface foundations. Package license files are included in installed distributions.
- GSAP: paused camera timelines manually driven by the graph renderer; consult the package's current license for redistribution terms.
- Michroma, Instrument Sans and IBM Plex Mono: self-hosted font packages and their included font licenses.

The procedural nebula shader, celestial representation, UI, synthetic dataset and application-specific database queries are newly authored implementation work. No reference-demo source was copied into the app. The original ShadowChain Python application remains a separate pre-existing repository component. No claim is made that this work occurred within a particular hackathon eligibility window.
