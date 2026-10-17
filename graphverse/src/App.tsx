import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Compass,
  Database,
  Expand,
  Eye,
  Fingerprint,
  Focus,
  GitBranch,
  Globe2,
  Layers3,
  LoaderCircle,
  Maximize,
  Orbit,
  Pause,
  Play,
  Radio,
  Search,
  Settings2,
  Shield,
  Sparkles,
  X,
} from 'lucide-react';
import { api, ApiError } from './api';
import { useExplorer, colors, type Quality } from './store';
import type { Entity, PathData } from '../shared/types';
const Universe = lazy(() => import('./Universe'));
class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="scene-fallback">
        WebGL is unavailable. Search, evidence and saved investigations remain available.
      </div>
    ) : (
      this.props.children
    );
  }
}
const icons = {
  infrastructure: Globe2,
  identity: Fingerprint,
  threat: Shield,
  memory: Orbit,
  incident: GitBranch,
};
function IconButton({
  label,
  children,
  onClick,
  active = false,
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      className={`icon-button ${active ? 'active' : ''}`}
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
export default function App() {
  const s = useExplorer(),
    [connected, setConnected] = useState(false),
    [login, setLogin] = useState(false),
    [token, setToken] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [query, setQuery] = useState(''),
    [question, setQuestion] = useState(''),
    [from, setFrom] = useState(''),
    [to, setTo] = useState(''),
    [settings, setSettings] = useState(false),
    [searchOpen, setSearchOpen] = useState(false),
    [saveTitle, setSaveTitle] = useState(''),
    [saving, setSaving] = useState(false),
    [journeySaveKey, setJourneySaveKey] = useState(crypto.randomUUID()),
    [queryTime, setQueryTime] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  async function run<T>(operation: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError('');
    try {
      return await operation();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to complete this operation.');
      if (e instanceof ApiError && [401, 503].includes(e.status)) setConnected(false);
      if (e instanceof ApiError && e.status === 401) setLogin(true);
    } finally {
      setBusy(false);
    }
  }
  async function load() {
    const start = performance.now();
    const [graph, clusters, investigations] = await Promise.all([
      api.graph(),
      api.clusters(),
      api.investigations(),
    ]);
    s.set({ graph, clusters, investigations });
    const selectable = graph.nodes.filter((node) => node.type !== 'Cluster');
    setFrom((value) =>
      selectable.some((node) => node.id === value)
        ? value
        : (selectable.find((node) => node.id === 'alice' && node.synthetic)?.id ??
          selectable[0]?.id ??
          ''),
    );
    setTo((value) =>
      selectable.some((node) => node.id === value)
        ? value
        : (selectable.find((node) => node.id === 'vault' && node.synthetic)?.id ??
          selectable.at(-1)?.id ??
          ''),
    );
    setQueryTime(Math.round(performance.now() - start));
    setConnected(true);
    setLogin(false);
    if (!sessionStorage.getItem('graphverse-intro-seen') && useExplorer.getState().motion) {
      s.set({ cameraAction: { kind: 'intro', nonce: Date.now() } });
      sessionStorage.setItem('graphverse-intro-seen', 'true');
    }
  }
  useEffect(() => {
    void api
      .session()
      .then(() => run(load))
      .catch(() => setLogin(true));
    const keyboard = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === 'Escape') {
        s.set({ panel: null, selected: null });
        setSearchOpen(false);
        setSettings(false);
      }
    };
    addEventListener('keydown', keyboard);
    return () => removeEventListener('keydown', keyboard);
  }, []);
  useEffect(() => {
    if (!connected) return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      void api.ready().catch((error: unknown) => {
        setConnected(false);
        setError(
          'Connection lost. The visible graph is the last loaded snapshot. Reconnect to continue.',
        );
        if (error instanceof ApiError && error.status === 401) setLogin(true);
      });
    }, 30000);
    return () => clearInterval(timer);
  }, [connected]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  const choose = (node: Entity) => {
    s.focus(node);
    setSearchOpen(false);
    setQuery('');
  };
  const showPath = (path: PathData) => {
    s.set({
      path,
      panel: 'path',
      journey: 'idle',
      step: 0,
      selected: null,
      hiddenClusters: [],
      pulse: { edgeIds: path.ordered_edge_ids, at: performance.now() },
    });
    setJourneySaveKey(crypto.randomUUID());
    setSaveTitle('');
  };
  async function trace() {
    await run(async () => {
      const path = await api.path(from, to);
      showPath(path);
      setQueryTime(path.query_ms);
    });
  }
  async function ask() {
    if (!question.trim()) return;
    await run(async () => {
      const answer = await api.ask(question);
      s.set({
        answer,
        panel: 'answer',
        selected: null,
        hiddenClusters: [],
        path: answer.path ?? null,
        journey: 'idle',
        step: 0,
        pulse: { edgeIds: answer.evidence.map((e) => e.id), at: performance.now() },
      });
      setQuestion('');
    });
  }
  const overview = () =>
    s.set({
      cameraAction: { kind: 'overview', nonce: Date.now() },
      activeCluster: null,
      hiddenClusters: [],
      journey: 'idle',
      intro: false,
    });
  const startJourney = () => {
    if (!s.path?.found) return;
    if (!s.motion) {
      s.set({
        journey: 'finished',
        step: s.path.nodes.length - 1,
        cameraAction: { kind: 'focus', id: s.path.nodes.at(-1)?.id, nonce: Date.now() },
      });
      return;
    }
    s.set({ journey: 'playing', journeySession: Date.now(), panel: 'path', intro: false, step: 0 });
  };
  const filtered = (s.graph?.nodes ?? [])
    .filter(
      (n) =>
        n.label.toLowerCase().includes(query.toLowerCase()) ||
        n.type.toLowerCase().includes(query.toLowerCase()),
    )
    .slice(0, 12);
  const neighbors =
    s.graph?.edges.filter((e) => e.source === s.selected?.id || e.target === s.selected?.id) ?? [];
  const statusColor = connected ? '#76e2bf' : '#e7ad7f';
  return (
    <main className="app">
      <SceneBoundary>
        <Suspense fallback={<div className="scene-loading">Opening the observatory…</div>}>
          <Universe />
        </Suspense>
      </SceneBoundary>
      <div className="vignette" />
      <header className="topbar">
        <button className="brand" onClick={overview} aria-label="GraphVerse overview">
          <span className="brand-mark">
            <Orbit size={25} />
          </span>
          <span>
            <strong>
              GRAPHVERSE<span className="brand-dot">.</span>
            </strong>
            <small>CONNECTED INTELLIGENCE</small>
          </span>
        </button>
        <div className="search-wrap">
          <Search size={16} />
          <input
            ref={searchRef}
            aria-label="Search entities"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => setSearchOpen(true)}
            placeholder="Find a world. Discover a connection."
          />
          <kbd>⌘ K</kbd>
          {searchOpen && (
            <div className="search-results glass">
              <div className="eyebrow">{query ? 'MATCHING ENTITIES' : 'EXPLORE YOUR UNIVERSE'}</div>
              {filtered.map((n) => (
                <button key={n.id} onClick={() => choose(n)}>
                  <i style={{ background: colors[n.cluster] }} />
                  <span>
                    {n.label}
                    <small>{n.type}</small>
                  </span>
                  <ArrowUpRight size={14} />
                </button>
              ))}
              {!filtered.length && (
                <p>
                  {connected
                    ? s.graph?.nodes.length
                      ? 'No matching entities. Search by a name from your inventory.'
                      : 'No inventory is connected yet.'
                    : 'Connect to FalkorDB to search entities.'}
                </p>
              )}
              <button className="quiet" onClick={() => setSearchOpen(false)}>
                Close search
              </button>
            </div>
          )}
        </div>
        <div className="connection">
          <span className="status-light" style={{ background: statusColor }} />
          <span>
            <strong>{connected ? 'FalkorDB connected' : 'Universe offline'}</strong>
            <small>
              {s.graph
                ? `${s.graph.nodes.length} entities · ${s.graph.edges.length} relationships`
                : 'Connect to explore'}
            </small>
          </span>
          <IconButton label="Connection settings" onClick={() => setLogin(true)}>
            <Database size={16} />
          </IconButton>
        </div>
      </header>
      <section className="universe-heading">
        <div className="eyebrow">
          <span className="tiny-line" />
          LIVE GRAPH EXPLORER
        </div>
        <h1>
          The connected universe<span>.</span>
        </h1>
        <p>Every world has a story. Follow the connections.</p>
        <div className="universe-tag">
          <span className="tag-orbit" /> SHADOWCHAIN /{' '}
          {s.graph?.nodes.some((node) => node.synthetic) ? 'SYNTHETIC UNIVERSE' : 'CONNECTED GRAPH'}{' '}
          <ChevronDown size={12} />
        </div>
      </section>
      <nav className="dock glass" aria-label="Explorer views">
        <IconButton
          label="Universe overview"
          active={!s.panel}
          onClick={() => {
            overview();
            s.set({ panel: null });
          }}
        >
          <Compass />
        </IconButton>
        <IconButton
          label="Cluster galaxies"
          active={s.panel === 'clusters'}
          onClick={() => s.set({ panel: s.panel === 'clusters' ? null : 'clusters' })}
        >
          <Layers3 />
        </IconButton>
        <IconButton
          label="Trace a relationship"
          active={s.panel === 'path'}
          onClick={() => s.set({ panel: s.panel === 'path' ? null : 'path' })}
        >
          <GitBranch />
        </IconButton>
        <IconButton
          label="Memory orbit"
          active={s.panel === 'memory'}
          onClick={() => {
            s.set({ panel: s.panel === 'memory' ? null : 'memory' });
            if (connected)
              void run(async () => s.set({ investigations: await api.investigations() }));
          }}
        >
          <Orbit />
        </IconButton>
        <span className="dock-rule" />
        <IconButton
          label="Explorer guide"
          active={s.panel === 'help'}
          onClick={() => s.set({ panel: s.panel === 'help' ? null : 'help' })}
        >
          <CircleHelp />
        </IconButton>
      </nav>
      {!s.panel && (
        <aside className="discovery-card">
          <div className="eyebrow">
            <Sparkles size={13} /> A CONNECTION WORTH EXPLORING
          </div>
          <h2>
            From identity
            <br />
            to hidden exposure.
          </h2>
          <p>
            {s.graph?.nodes.some((node) => node.id === 'alice' && node.synthetic)
              ? 'Follow Alice Chen’s relationships to the Research vault.'
              : 'Connect your inventory, then select two entities to inspect their relationships.'}
          </p>
          <button
            className="text-action"
            disabled={!connected || busy || !s.graph?.nodes.length}
            onClick={() => {
              if (s.graph?.nodes.some((node) => node.id === 'alice' && node.synthetic)) {
                setFrom('alice');
                setTo('vault');
                void run(async () => showPath(await api.path('alice', 'vault')));
              } else s.set({ panel: 'path' });
            }}
          >
            Discover the path <ArrowUpRight size={17} />
          </button>
          <div className="mini-route">
            <span />
            <i />
            <span />
            <i />
            <span />
            <i />
            <span className="destination" />
          </div>
        </aside>
      )}
      {s.panel && (
        <aside className="inspector glass" aria-label="Intelligence inspector">
          <div className="panel-heading">
            <div className="eyebrow">
              {s.panel === 'entity'
                ? 'ENTITY INTELLIGENCE'
                : s.panel === 'path'
                  ? 'HYPERDRIVE TRAVERSAL'
                  : s.panel === 'answer'
                    ? 'COSMIC INTELLIGENCE'
                    : s.panel === 'clusters'
                      ? 'CLUSTER GALAXIES'
                      : s.panel === 'memory'
                        ? 'MEMORY ORBIT'
                        : 'FLIGHT MANUAL'}
            </div>
            <IconButton label="Close inspector" onClick={() => s.set({ panel: null })}>
              <X size={16} />
            </IconButton>
          </div>
          {s.panel === 'entity' && s.selected && (
            <>
              <div
                className="entity-icon"
                style={{ '--entity-color': colors[s.selected.cluster] } as React.CSSProperties}
              >
                <Globe2 size={35} />
              </div>
              <div className="eyebrow muted">{s.selected.type}</div>
              <h2>{s.selected.label}</h2>
              <p>{s.selected.description}</p>
              <div className="pills">
                <span>{s.selected.synthetic ? 'Synthetic' : 'Imported observation'}</span>
                <span>
                  {s.selected.severity === 'none' ? 'Unassessed' : `${s.selected.severity} signal`}
                </span>
              </div>
              <dl>
                <dt>Universe ID</dt>
                <dd>{s.selected.id}</dd>
                <dt>Source</dt>
                <dd>{s.selected.source}</dd>
                <dt>Connected edges</dt>
                <dd>{neighbors.length}</dd>
              </dl>
              <button
                className="primary wide"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const fresh = await api.neighbors(s.selected!.id);
                    const current = s.graph!;
                    const mergedNodes = Array.from(
                        new Map([...current.nodes, ...fresh.nodes].map((n) => [n.id, n])).values(),
                      ),
                      mergedEdges = Array.from(
                        new Map([...current.edges, ...fresh.edges].map((e) => [e.id, e])).values(),
                      );
                    s.set({
                      graph: { ...current, nodes: mergedNodes, edges: mergedEdges },
                      hiddenClusters: [],
                      pulse: { edgeIds: fresh.edges.map((e) => e.id), at: performance.now() },
                    });
                    setNotice(
                      `FalkorDB returned ${fresh.edges.length} neighboring relationships${fresh.truncated ? ' (bounded result)' : ''}.`,
                    );
                  })
                }
              >
                <Expand size={15} /> Expand neighbors
              </button>
              <div className="split-buttons">
                <button
                  onClick={() => {
                    setFrom(s.selected!.id);
                    s.set({ panel: 'path' });
                  }}
                >
                  Set origin
                </button>
                <button
                  onClick={() => {
                    setTo(s.selected!.id);
                    s.set({ panel: 'path' });
                  }}
                >
                  Set destination
                </button>
              </div>
              <h3>Connected evidence</h3>
              {neighbors
                .filter((e) => e.type !== 'MEMBER_OF')
                .slice(0, 8)
                .map((e) => (
                  <div className="evidence-row" key={e.id}>
                    <span>{e.type.replaceAll('_', ' ')}</span>
                    <p>{e.evidence}</p>
                    <small>{Math.round(e.confidence * 100)}% observation confidence</small>
                  </div>
                ))}
            </>
          )}
          {s.panel === 'clusters' && (
            <>
              <h2>
                Five regions.
                <br />
                One connected graph.
              </h2>
              <p>Curated semantic groups, connected by relationships in FalkorDB.</p>
              {s.clusters.map((c) => {
                const Icon = icons[c.id as keyof typeof icons] ?? Globe2;
                return (
                  <div className="cluster-row" key={c.id}>
                    <button
                      onClick={() =>
                        s.set({
                          activeCluster: c.id,
                          hiddenClusters: s.hiddenClusters.filter((id) => id !== c.id),
                          cameraAction: { kind: 'cluster', id: c.id, nonce: Date.now() },
                        })
                      }
                    >
                      <Icon style={{ color: colors[c.id] }} size={22} />
                      <span>
                        {c.label}
                        <small>{c.count} entities · curated</small>
                      </span>
                      <ChevronRight size={15} />
                    </button>
                    <p>{c.description}</p>
                    <div className="cluster-actions">
                      <button
                        onClick={() =>
                          s.set({
                            hiddenClusters: s.hiddenClusters.includes(c.id)
                              ? s.hiddenClusters.filter((id) => id !== c.id)
                              : [...s.hiddenClusters, c.id],
                          })
                        }
                      >
                        {s.hiddenClusters.includes(c.id) ? 'Reveal region' : 'Collapse region'}
                      </button>
                      <button
                        onClick={() =>
                          s.set({
                            hiddenClusters: s.clusters
                              .filter((x) => x.id !== c.id)
                              .map((x) => x.id),
                            activeCluster: c.id,
                            cameraAction: { kind: 'cluster', id: c.id, nonce: Date.now() },
                          })
                        }
                      >
                        Isolate
                      </button>
                    </div>
                  </div>
                );
              })}
              <button className="quiet wide" onClick={overview}>
                Reveal all regions
              </button>
            </>
          )}
          {s.panel === 'path' && (
            <>
              <h2>Follow the evidence.</h2>
              <p>A real relationship path. A different perspective.</p>
              <label className="field-label">
                ORIGIN
                <select
                  aria-label="Path origin"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                >
                  {s.graph?.nodes
                    .filter((n) => n.type !== 'Cluster')
                    .map((n) => (
                      <option value={n.id} key={n.id}>
                        {n.label}
                      </option>
                    ))}
                </select>
              </label>
              <div className="path-arrow">
                <ArrowDown size={15} />
              </div>
              <label className="field-label">
                DESTINATION
                <select
                  aria-label="Path destination"
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                >
                  {s.graph?.nodes
                    .filter((n) => n.type !== 'Cluster')
                    .map((n) => (
                      <option value={n.id} key={n.id}>
                        {n.label}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="primary wide"
                disabled={!connected || busy || !from || !to}
                onClick={() => void trace()}
              >
                <GitBranch size={16} /> Trace relationship
              </button>
              {s.path && <PathEvidence path={s.path} step={s.step} />}
              {s.path?.found && (
                <>
                  <button
                    className="launch wide"
                    onClick={startJourney}
                    disabled={s.journey === 'playing'}
                  >
                    <Play size={15} />
                    {s.motion ? 'Begin hyperdrive' : 'Show destination'}
                  </button>
                  <label className="field-label save-label">
                    SAVE THIS INVESTIGATION
                    <input
                      aria-label="Investigation title"
                      value={saveTitle}
                      onChange={(e) => setSaveTitle(e.target.value)}
                      placeholder="Give this discovery a name"
                      maxLength={120}
                    />
                  </label>
                  <button
                    className="quiet wide"
                    disabled={saving || !saveTitle.trim()}
                    onClick={() => {
                      setSaving(true);
                      void run(async () => {
                        const p = s.path!;
                        await api.save(
                          journeySaveKey,
                          saveTitle,
                          p.ordered_node_ids[0],
                          p.ordered_node_ids.at(-1)!,
                        );
                        s.set({ investigations: await api.investigations() });
                        setNotice('Investigation and evidence saved in FalkorDB.');
                        setSaveTitle('');
                      }).finally(() => setSaving(false));
                    }}
                  >
                    <BookOpen size={14} /> Save to memory
                  </button>
                </>
              )}
            </>
          )}
          {s.panel === 'answer' && (
            <>
              <div className="assistant-symbol">
                <Sparkles />
              </div>
              <h2>Connected intelligence.</h2>
              <div className="pills">
                <span>Deterministic graph assistant</span>
                <span>No LLM key required</span>
              </div>
              <p className="answer-copy">
                {s.answer?.answer ??
                  'Ask a question using entity names, or choose two entities to trace their connection.'}
              </p>
              {s.answer?.model_note && (
                <p>
                  <strong>Gemini explanation:</strong> {s.answer.model_note}
                </p>
              )}
              {s.answer?.path && <PathEvidence path={s.answer.path} step={s.step} />}{' '}
              {!!s.answer?.path?.found && (
                <button className="launch wide" onClick={startJourney}>
                  <Play size={14} /> Travel this path
                </button>
              )}
              {s.answer?.entities.map((n) => (
                <button className="entity-link" key={n.id} onClick={() => choose(n)}>
                  <span style={{ color: colors[n.cluster] }}>●</span>
                  {n.label}
                  <ArrowUpRight size={13} />
                </button>
              ))}
              {s.answer?.evidence.map((e) => (
                <div className="evidence-row" key={e.id}>
                  <span>{e.type}</span>
                  <p>{e.evidence}</p>
                </div>
              ))}
            </>
          )}
          {s.panel === 'memory' && (
            <>
              <h2>Discoveries remembered.</h2>
              <p>Your investigations and their exact evidence paths, persisted in FalkorDB.</p>
              {s.investigations.length ? (
                s.investigations.map((i) => (
                  <button
                    className="memory-card"
                    key={i.id}
                    onClick={() =>
                      void run(async () => {
                        showPath(await api.savedPath(i.id));
                        setNotice('Loaded preserved investigation evidence.');
                      })
                    }
                  >
                    <Orbit size={22} />
                    <span>
                      <strong>{i.title}</strong>
                      <small>
                        {i.node_ids.length} entities · {new Date(i.created_at).toLocaleDateString()}
                      </small>
                    </span>
                    <ArrowUpRight size={15} />
                  </button>
                ))
              ) : (
                <div className="empty-state">
                  <Orbit size={30} />
                  <h3>Your next discovery belongs here.</h3>
                  <p>
                    Trace a relationship, then save the investigation. It will be here when you
                    return.
                  </p>
                </div>
              )}
              <button className="quiet wide" onClick={() => s.set({ panel: 'path' })}>
                Trace a new relationship <ArrowRight size={14} />
              </button>
            </>
          )}
          {s.panel === 'help' && (
            <>
              <h2>Welcome to the observatory.</h2>
              <p>
                Space is a visual metaphor. Distance represents graph layout, not geography, time or
                access probability.
              </p>
              <dl>
                <dt>Rotate</dt>
                <dd>Drag empty space</dd>
                <dt>Zoom</dt>
                <dd>Scroll / pinch</dd>
                <dt>Pan</dt>
                <dd>Right drag</dd>
                <dt>Inspect & focus</dt>
                <dd>Click an entity</dd>
                <dt>Search</dt>
                <dd>Ctrl / ⌘ K</dd>
                <dt>Close panel</dt>
                <dd>Escape</dd>
              </dl>
              <h3>A guided discovery</h3>
              <ol>
                <li>Select two connected entities and trace a path.</li>
                <li>Inspect the evidence for each connection.</li>
                <li>Start Hyperdrive and follow the actual path.</li>
                <li>Save the investigation to Memory orbit.</li>
                <li>Reload and reopen the preserved evidence.</li>
              </ol>
              <p className="fineprint">
                Potential access is not proven effective authorization. Memory conflicts remain
                unverified. The assistant only retrieves graph evidence; it cannot execute commands.
              </p>
              <button
                className="primary wide"
                onClick={() => {
                  s.set({ panel: null, cameraAction: { kind: 'intro', nonce: Date.now() } });
                  sessionStorage.setItem('graphverse-intro-seen', 'true');
                }}
              >
                <Play size={15} /> Play cinematic introduction
              </button>
              <button className="quiet wide" onClick={() => setLogin(true)}>
                Manage database connection
              </button>
            </>
          )}
        </aside>
      )}
      {s.intro && (
        <div className="intro-caption glass">
          <span className="eyebrow">WELCOME TO GRAPHVERSE</span>
          <strong>Explore the universe hidden inside your data.</strong>
          <button
            onClick={() => {
              s.set({ intro: false });
              overview();
            }}
          >
            Skip intro <ArrowRight size={14} />
          </button>
        </div>
      )}
      {(s.journey === 'playing' || s.journey === 'paused' || s.journey === 'finished') &&
        s.path && (
          <div className="journey-hud glass">
            <div>
              <span className="eyebrow">
                {s.journey === 'finished' ? 'DESTINATION REACHED' : 'HYPERDRIVE ACTIVE'}
              </span>
              <strong>{s.path.nodes[s.step]?.label}</strong>
              <small>
                {s.step + 1} / {s.path.nodes.length} entities ·{' '}
                {s.step ? s.path.edges[s.step - 1]?.type : 'Origin'}
              </small>
            </div>
            <IconButton
              label={s.journey === 'playing' ? 'Pause journey' : 'Resume journey'}
              onClick={() => s.set({ journey: s.journey === 'playing' ? 'paused' : 'playing' })}
            >
              {s.journey === 'playing' ? <Pause size={17} /> : <Play size={17} />}
            </IconButton>
            <button
              className="quiet"
              onClick={() => {
                s.set({
                  journey: 'finished',
                  step: s.path!.nodes.length - 1,
                  cameraAction: { kind: 'focus', id: s.path!.nodes.at(-1)?.id, nonce: Date.now() },
                });
              }}
            >
              Skip
            </button>
            <IconButton label="Return to overview" onClick={overview}>
              <Maximize size={17} />
            </IconButton>
          </div>
        )}
      <footer className="bottom-interface">
        <div className="legend">
          <span className="eyebrow">THE UNIVERSE, DECODED</span>
          <div>
            {Object.entries(colors).map(([id, color]) => (
              <button
                key={id}
                onClick={() =>
                  s.set({
                    panel: 'clusters',
                    activeCluster: id,
                    cameraAction: { kind: 'cluster', id, nonce: Date.now() },
                  })
                }
              >
                <i style={{ background: color }} />
                {id === 'infrastructure'
                  ? 'Infrastructure'
                  : id.charAt(0).toUpperCase() + id.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <form
          className="prompt glass"
          onSubmit={(e) => {
            e.preventDefault();
            void ask();
          }}
        >
          <span className="prompt-icon">
            <Sparkles size={20} />
          </span>
          <div>
            <label htmlFor="question">ASK YOUR UNIVERSE</label>
            <input
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="How are two entities in your graph connected?"
              maxLength={1000}
            />
          </div>
          <button
            aria-label="Ask graph assistant"
            disabled={busy || !connected || !question.trim()}
          >
            {busy ? <LoaderCircle className="spin" size={19} /> : <ArrowRight size={20} />}
          </button>
        </form>
        <div className="display-controls glass">
          <IconButton label="Fit universe" onClick={overview}>
            <Focus size={19} />
          </IconButton>
          <IconButton
            label="Toggle labels"
            active={s.labels}
            onClick={() => s.set({ labels: !s.labels })}
          >
            <Eye size={18} />
          </IconButton>
          <IconButton
            label="Display settings"
            active={settings}
            onClick={() => setSettings(!settings)}
          >
            <Settings2 size={18} />
          </IconButton>
        </div>
      </footer>
      <div className="statusbar">
        <span>
          <Radio size={11} /> {connected ? 'LIVE RELATIONSHIPS' : 'AWAITING CONNECTION'} <i />{' '}
          {s.graph?.nodes.some((node) => node.synthetic)
            ? 'CONTAINS SYNTHETIC DATA'
            : s.graph?.nodes.length
              ? 'IMPORTED OBSERVATIONS'
              : 'NO INVENTORY CONNECTED'}
        </span>
        <span className="interaction-hint">
          DRAG TO ORBIT <b>·</b> SCROLL TO EXPLORE <b>·</b> CLICK TO DISCOVER
        </span>
        <span>
          {queryTime !== null ? `${queryTime} ms last request` : 'FALKORDB POWERED'} <i />{' '}
          {s.quality.toUpperCase()}
        </span>
      </div>
      {settings && (
        <div className="settings-panel glass">
          <div className="panel-heading">
            <span className="eyebrow">DISPLAY & MOTION</span>
            <IconButton label="Close display settings" onClick={() => setSettings(false)}>
              <X size={14} />
            </IconButton>
          </div>
          <label>
            Render quality
            <select
              value={s.quality}
              onChange={(e) => s.set({ quality: e.target.value as Quality })}
            >
              <option value="cinematic">Cinematic</option>
              <option value="balanced">Balanced</option>
              <option value="performance">Performance</option>
            </select>
          </label>
          {[
            {
              label: 'Cinematic bloom',
              value: s.bloom,
              onChange: () => s.set({ bloom: !s.bloom }),
            },
            {
              label: 'Relationship direction',
              value: s.direction,
              onChange: () => s.set({ direction: !s.direction }),
            },
            {
              label: 'Ambient motion',
              value: s.motion,
              onChange: () => s.set({ motion: !s.motion, journey: 'idle' }),
            },
          ].map((x) => (
            <label className="toggle-row" key={x.label}>
              {x.label}
              <input type="checkbox" checked={x.value} onChange={x.onChange} />
            </label>
          ))}
          <p>
            Performance mode disables bloom. Layout is a visual metaphor, never a physical distance.
          </p>
        </div>
      )}
      {(error || notice) && (
        <div className={`toast glass ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>
          {error ? <Shield size={16} /> : <Check size={16} />}
          <span>{error || notice}</span>
          <button
            aria-label="Dismiss notification"
            onClick={() => {
              setError('');
              setNotice('');
            }}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {login && (
        <div className="modal-scrim">
          <form
            className="connect-modal glass"
            role="dialog"
            aria-modal="true"
            aria-labelledby="connect-title"
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setLogin(false);
                return;
              }
              if (event.key !== 'Tab') return;
              const items = Array.from(
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'button:not(:disabled), input:not(:disabled)',
                ),
              );
              const first = items[0],
                last = items.at(-1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api.login(token);
                setToken('');
                await load();
              });
            }}
          >
            <div className="panel-heading">
              <span className="eyebrow">YOUR PRIVATE OBSERVATORY</span>
              <IconButton label="Close connection dialog" onClick={() => setLogin(false)}>
                <X size={16} />
              </IconButton>
            </div>
            <div className="connect-orbit">
              <Orbit size={40} />
            </div>
            <h2 id="connect-title">Connect your universe.</h2>
            <p>
              Enter your operator token to explore your FalkorDB graph. Credentials stay out of
              graph data and browser storage.
            </p>
            <label className="field-label">
              OPERATOR TOKEN
              <input
                type="password"
                autoFocus
                aria-label="Operator token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
                placeholder="Paste your private operator token"
                required
                minLength={32}
              />
            </label>
            <button className="primary wide" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={16} /> : <Database size={16} />} Connect
              to FalkorDB
            </button>
            <p className="fineprint">
              Use the token created by the local setup script. No AWS account or AI API key
              required.
            </p>
            {connected && (
              <button
                type="button"
                className="quiet wide"
                onClick={() =>
                  void run(async () => {
                    await fetch('/api/session', {
                      method: 'DELETE',
                      headers: { 'Content-Type': 'application/json' },
                    });
                    s.set({
                      graph: null,
                      clusters: [],
                      investigations: [],
                      selected: null,
                      path: null,
                      answer: null,
                      panel: null,
                      journey: 'idle',
                    });
                    setConnected(false);
                    setToken('');
                  })
                }
              >
                Disconnect session
              </button>
            )}
          </form>
        </div>
      )}
    </main>
  );
}
function PathEvidence({ path, step }: { path: PathData; step: number }) {
  return (
    <section className="path-evidence">
      <div className="result-heading">
        <span className={path.found ? 'result-status' : 'muted'}>
          {path.found ? 'PATH DISCOVERED' : 'NO BOUNDED PATH'}
        </span>
        <span>{path.query_ms} ms</span>
      </div>
      <p className="fineprint">{path.explanation}</p>
      {path.nodes.map((n, i) => (
        <div className={`path-stop ${i === step ? 'current' : ''}`} key={`${n.id}-${i}`}>
          <div className="stop-dot" style={{ borderColor: colors[n.cluster] }} />
          <button onClick={() => useExplorer.getState().focus(n)}>
            <strong>{n.label}</strong>
            <small>{n.type}</small>
          </button>
          {path.edges[i] && (
            <div className="path-connection">
              <span>{path.edges[i].type.replaceAll('_', ' ')}</span>
              <details>
                <summary>View evidence</summary>
                <p>{path.edges[i].evidence}</p>
                <small>
                  {path.edges[i].valid_from} · {Math.round(path.edges[i].confidence * 100)}%
                  observation confidence
                </small>
              </details>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
