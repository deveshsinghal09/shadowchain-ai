import { useEffect, useState } from 'react';
import { RefreshCw, GitBranch, ArrowUpRight } from 'lucide-react';
import { api } from './api';
import type { LiveActivity, LiveStatus } from '../shared/types';

export default function LivePanel({
  operator,
  onLogin,
  onRefresh,
}: {
  operator: boolean;
  onLogin: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [status, setStatus] = useState<LiveStatus | null>(null),
    [activity, setActivity] = useState<LiveActivity | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  async function refresh() {
    const [s, a] = await Promise.all([api.liveStatus(), api.activity()]);
    setStatus(s);
    setActivity(a);
  }
  useEffect(() => {
    let active = true;
    const update = () => {
      if (active)
        void refresh().catch(() => {
          if (active) setError('Could not load persisted source activity. Try refreshing.');
        });
    };
    update();
    const timer = setInterval(update, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  const source = status?.sources[0],
    run = activity?.runs[0];
  async function sync() {
    if (!operator) {
      onLogin();
      return;
    }
    const repository = status?.repositories[0];
    if (!repository) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api.sync(repository);
      setStatus(result);
      await refresh();
      await onRefresh();
      setMessage(
        result.duplicate
          ? 'Source checked. No inventory changes; completed tasks were retained.'
          : 'GitHub inventory and investigation observations saved in FalkorDB.',
      );
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'Synchronization failed. The last good graph is preserved.',
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="live-panel">
      <div className="entity-icon">
        <GitBranch size={22} />
      </div>
      <h2>Connected to the source.</h2>
      <p>
        Read-only public GitHub evidence. Refresh deliberately, follow its provenance, and review
        what the agents recorded.
      </p>
      {!status && !error && <p role="status">Loading source and persisted activity…</p>}
      {status && !status.enabled && (
        <p>
          No repository is allowlisted on the server yet. An operator must configure the source
          before synchronization.
        </p>
      )}
      {status?.repositories.map((repository) => (
        <a
          className="source-link"
          key={repository}
          href={`https://github.com/${repository}`}
          target="_blank"
          rel="noreferrer"
        >
          {repository}
          <ArrowUpRight size={14} />
        </a>
      ))}
      <dl>
        <dt>Connection</dt>
        <dd>{source ? 'Imported public inventory' : 'Awaiting first import'}</dd>
        <dt>Last checked (UTC)</dt>
        <dd>{source?.last_checked_at ?? 'Not checked'}</dd>
        <dt>Source commit</dt>
        <dd>{source?.head?.slice(0, 12) ?? '—'}</dd>
        <dt>Refresh mode</dt>
        <dd>Operator-triggered · GitHub reads only</dd>
        <dt>Investigation</dt>
        <dd>{run ? `${run.status} · ${run.cursor}/6 stages` : 'Not started'}</dd>
      </dl>
      {status?.enabled && (
        <button className="primary wide" disabled={busy} onClick={() => void sync()}>
          <RefreshCw size={14} />
          {busy
            ? 'Reading GitHub and persisting evidence…'
            : operator
              ? 'Sync GitHub now'
              : 'Operator sign-in to sync'}
        </button>
      )}
      {message && (
        <p role="status" className="source-message">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="source-error">
          {error}
        </p>
      )}
      {run && (
        <>
          <h3>Agent activity</h3>
          <p className="fineprint">
            Deterministic repository investigation stages. These records come from FalkorDB, not
            animated status messages.
          </p>
          <ol className="source-tasks">
            {activity?.tasks
              .filter((t) => t.run_id === run.id)
              .sort((a, b) => a.step - b.step)
              .map((task) => (
                <li key={task.id}>
                  <strong>{task.agent_name}</strong>
                  <span>
                    {task.status} · {task.retrieved_memory_ids.length} memory reads
                  </span>
                  <p>{task.output}</p>
                  <small>
                    Evidence: {task.input_evidence_ids.length} · Output memory:{' '}
                    {task.output_memory_id.slice(-10)}
                  </small>
                </li>
              ))}
          </ol>
          <p className="fineprint">
            {activity?.handoffs.filter((h) => h.run_id === run.id).length ?? 0} persisted handoffs ·
            completed tasks survive API restarts.
          </p>
          {operator && run.cursor < 6 && (
            <button
              className="primary wide"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void api
                  .resume(run.id)
                  .then(async () => {
                    await refresh();
                    await onRefresh();
                  })
                  .catch((e) => setError(e.message))
                  .finally(() => setBusy(false));
              }}
            >
              Resume unfinished stages
            </button>
          )}
        </>
      )}
      {source && (
        <details>
          <summary>Coverage and limitations</summary>
          <ul>
            {source.coverage.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
