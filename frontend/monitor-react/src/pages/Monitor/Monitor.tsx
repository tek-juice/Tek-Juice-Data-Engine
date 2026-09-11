import { useState, useEffect, useCallback, useRef } from 'react';
import { getMetricsErrors, getPipelineMetrics, getActivityLog } from '../../api/dashboard';
import { listWebhooks, getWebhookLogs } from '../../api/auth';
import { getTelemetrySummary, getQueueStatus } from '../../api/telemetry';
import { ActivityFeedSocket } from '../../services/websocket';
import type {
  TelemetryError,
  PipelineMetrics,
  ActivityLogEntry,
  WebhookEndpoint,
  WebhookLogEntry,
  TelemetrySummary,
  QueueStatus,
} from '../../types';

// ── Tiny shared primitives (matching Dashboard.tsx style) ─────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-lg overflow-hidden ${className}`}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {children}
    </div>
  );
}

function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`rounded animate-pulse ${className}`}
      style={{ background: 'var(--surface-3)' }}
    />
  );
}

function SectionTitle({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-base font-bold" style={{ color: 'var(--text)' }}>{title}</h2>
      {description && (
        <p className="text-sm mt-0.5" style={{ color: 'var(--text-3)' }}>{description}</p>
      )}
    </div>
  );
}

// ── Status dot ────────────────────────────────────────────────────────────────

function Dot({ color }: { color: string }) {
  return (
    <span
      className="inline-block w-2 h-2 rounded-full flex-shrink-0"
      style={{ background: color }}
    />
  );
}

// ── WS status pill ────────────────────────────────────────────────────────────

function WsPill({ status }: { status: 'connected' | 'disconnected' | 'error' }) {
  const map = {
    connected:    { label: 'Live',         color: 'var(--success)' },
    disconnected: { label: 'Disconnected', color: 'var(--text-3)'  },
    error:        { label: 'Error',        color: 'var(--danger)'  },
  } as const;
  const { label, color } = map[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold"
      style={{ background: 'var(--surface-2)', color, border: '1px solid var(--border)' }}
    >
      <Dot color={color} />
      {label}
    </span>
  );
}

// ── Relative time helper ──────────────────────────────────────────────────────

function relativeTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Page header ───────────────────────────────────────────────────────────────

function PageHeader({
  lastUpdated, onRefresh, refreshing, wsStatus,
}: {
  lastUpdated: Date;
  onRefresh: () => void;
  refreshing: boolean;
  wsStatus: 'connected' | 'disconnected' | 'error';
}) {
  const date = lastUpdated.toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
  return (
    <header
      className="flex items-center justify-between px-7 py-4 flex-shrink-0"
      style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}
    >
      <div>
        <h1 className="text-xl font-bold" style={{ color: 'var(--text)', letterSpacing: '-0.01em' }}>
          Monitor
        </h1>
        <p className="text-sm mt-0.5" style={{ color: 'var(--text-3)' }}>
          Last updated {date}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <WsPill status={wsStatus} />
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="px-4 py-2 text-sm font-bold rounded transition-colors disabled:opacity-40"
          style={{ background: 'var(--brand)', color: '#111' }}
          onMouseEnter={e => { if (!refreshing) e.currentTarget.style.background = 'var(--brand-light)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'var(--brand)'; }}
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
    </header>
  );
}

// ── KPI stat card ─────────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, loading, accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  loading?: boolean;
  accent?: string;
}) {
  return (
    <Card className="p-5">
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-8 w-16" />
        </div>
      ) : (
        <>
          <p className="text-xs font-bold uppercase mb-1.5" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>
            {label}
          </p>
          <p className="text-3xl font-bold tabular-nums" style={{ color: accent ?? 'var(--text)' }}>
            {value}
          </p>
          {sub && (
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>{sub}</p>
          )}
        </>
      )}
    </Card>
  );
}

// ── Error log table ───────────────────────────────────────────────────────────

function ErrorLog({ errors, loading }: { errors: TelemetryError[]; loading: boolean }) {
  return (
    <Card className="overflow-hidden">
      <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2.5">
          <Dot color={errors.length > 0 ? 'var(--danger)' : 'var(--success)'} />
          <SectionTitle
            title="Error Log"
            description={errors.length > 0 ? `${errors.length} errors recorded` : 'No errors recorded'}
          />
        </div>
      </div>
      {loading ? (
        <div className="p-6 space-y-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : errors.length === 0 ? (
        <div className="px-6 py-10 text-center">
          <p className="text-sm" style={{ color: 'var(--text-3)' }}>No errors in the last 24 hours.</p>
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th className="py-3 px-6 text-left text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Service</th>
              <th className="py-3 px-6 text-left text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Error</th>
              <th className="py-3 px-6 text-right text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Time</th>
            </tr>
          </thead>
          <tbody>
            {errors.slice(0, 20).map((err, i) => (
              <tr
                key={i}
                style={{ borderBottom: '1px solid var(--border-subtle)', transition: 'background 0.12s' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
              >
                <td className="py-2.5 px-6 text-sm font-medium" style={{ color: 'var(--text)' }}>
                  {err.service ?? '—'}
                </td>
                <td className="py-2.5 px-6 text-sm max-w-md">
                  <span
                    className="block truncate"
                    style={{ color: 'var(--danger)' }}
                    title={err.error ?? ''}
                  >
                    {err.error ?? '—'}
                  </span>
                </td>
                <td className="py-2.5 px-6 text-sm text-right" style={{ color: 'var(--text-3)' }}>
                  {relativeTime(err.timestamp)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ── Pipeline latencies ────────────────────────────────────────────────────────

function PipelinePanel({ pipeline, loading }: { pipeline: PipelineMetrics | null; loading: boolean }) {
  const stages: [string, number][] = pipeline
    ? [
        ['Ingest', pipeline.ingest_ms],
        ['Chunk',  pipeline.chunk_ms],
        ['Embed',  pipeline.embed_ms],
        ['Gap',    pipeline.gap_ms],
        ['Write',  pipeline.write_ms],
      ]
    : [];

  return (
    <Card className="p-6">
      <SectionTitle title="Pipeline Stage Latencies" description="Average processing time per stage" />
      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-20" />)}
        </div>
      ) : !pipeline ? (
        <p className="text-sm" style={{ color: 'var(--text-3)' }}>No pipeline data available yet.</p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
          {stages.map(([stage, ms]) => (
            <div
              key={stage}
              className="p-4 text-center"
              style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 6 }}
            >
              <p className="text-xs font-bold uppercase mb-2" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>{stage}</p>
              <p
                className="text-xl font-bold tabular-nums"
                style={{ color: ms < 200 ? 'var(--success)' : ms < 500 ? 'var(--warning)' : 'var(--danger)' }}
              >
                {ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`}
              </p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── Webhook panel ─────────────────────────────────────────────────────────────

function WebhookRow({
  hook, selected, onSelect,
}: {
  hook: WebhookEndpoint;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <tr
      style={{
        borderBottom: '1px solid var(--border-subtle)',
        transition: 'background 0.12s',
        background: selected ? 'var(--brand-10)' : '',
        cursor: 'pointer',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--surface-2)'; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = ''; }}
      onClick={() => onSelect(hook.endpoint_id)}
    >
      <td className="py-2.5 px-6 text-sm">
        <span className="block truncate max-w-xs font-medium" style={{ color: 'var(--text)' }} title={hook.url}>
          {hook.url}
        </span>
      </td>
      <td className="py-2.5 px-6 text-sm" style={{ color: 'var(--text-2)' }}>
        {(hook.event_types ?? []).join(', ') || '—'}
      </td>
      <td className="py-2.5 px-6 text-sm text-center">
        <Dot color={hook.is_active ? 'var(--success)' : 'var(--text-3)'} />
      </td>
      <td className="py-2.5 px-6 text-sm text-right" style={{ color: 'var(--text-3)' }}>
        {relativeTime(hook.created_at)}
      </td>
    </tr>
  );
}

function WebhookLogs({ logs, loading }: { logs: WebhookLogEntry[]; loading: boolean }) {
  if (loading) return (
    <div className="p-4 space-y-2">
      {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
    </div>
  );
  if (logs.length === 0) return (
    <div className="px-6 py-6 text-center">
      <p className="text-sm" style={{ color: 'var(--text-3)' }}>No delivery logs found.</p>
    </div>
  );
  return (
    <table className="w-full text-sm">
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border)' }}>
          <th className="py-3 px-6 text-left text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Event</th>
          <th className="py-3 px-6 text-center text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Status</th>
          <th className="py-3 px-6 text-right text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Time</th>
        </tr>
      </thead>
      <tbody>
        {logs.slice(0, 20).map((log, i) => (
          <tr
            key={i}
            style={{ borderBottom: '1px solid var(--border-subtle)', transition: 'background 0.12s' }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
            onMouseLeave={e => (e.currentTarget.style.background = '')}
          >
            <td className="py-2.5 px-6 font-medium" style={{ color: 'var(--text)' }}>
              {log.event_type ?? '—'}
            </td>
            <td className="py-2.5 px-6 text-center">
              <span
                className="text-xs font-bold px-2 py-0.5 rounded"
                style={{
                  background: (log.status_code ?? 0) < 300
                    ? 'rgba(34,197,94,0.1)'
                    : 'rgba(239,68,68,0.1)',
                  color: (log.status_code ?? 0) < 300
                    ? 'var(--success)'
                    : 'var(--danger)',
                }}
              >
                {log.status_code ?? (log.success ? '2xx' : 'fail')}
              </span>
            </td>
            <td className="py-2.5 px-6 text-right" style={{ color: 'var(--text-3)' }}>
              {relativeTime(log.attempted_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function WebhookPanel({
  webhooks, loading,
}: {
  webhooks: WebhookEndpoint[];
  loading: boolean;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [logs, setLogs]             = useState<WebhookLogEntry[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const handleSelect = useCallback((id: string) => {
    if (selectedId === id) { setSelectedId(null); setLogs([]); return; }
    setSelectedId(id);
    setLogsLoading(true);
    getWebhookLogs(id, 20)
      .then(setLogs)
      .catch(() => setLogs([]))
      .finally(() => setLogsLoading(false));
  }, [selectedId]);

  return (
    <Card className="overflow-hidden">
      <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
        <SectionTitle
          title="Webhooks"
          description={webhooks.length > 0 ? `${webhooks.length} endpoint${webhooks.length !== 1 ? 's' : ''} registered — click a row to view delivery logs` : 'No webhook endpoints registered yet'}
        />
      </div>

      {loading ? (
        <div className="p-6 space-y-3">
          {[...Array(2)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : webhooks.length === 0 ? (
        <div className="px-6 py-10 text-center">
          <p className="text-sm" style={{ color: 'var(--text-3)' }}>
            No endpoints registered. Register one under API Credentials.
          </p>
        </div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th className="py-3 px-6 text-left text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>URL</th>
                <th className="py-3 px-6 text-left text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Events</th>
                <th className="py-3 px-6 text-center text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Active</th>
                <th className="py-3 px-6 text-right text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>Registered</th>
              </tr>
            </thead>
            <tbody>
              {webhooks.map(hook => (
                <WebhookRow
                  key={hook.endpoint_id}
                  hook={hook}
                  selected={selectedId === hook.endpoint_id}
                  onSelect={handleSelect}
                />
              ))}
            </tbody>
          </table>

          {selectedId && (
            <div style={{ borderTop: '1px solid var(--border)' }}>
              <div className="px-6 py-3" style={{ background: 'var(--surface-2)' }}>
                <p className="text-xs font-bold uppercase" style={{ color: 'var(--text-3)', letterSpacing: '0.06em' }}>
                  Delivery Logs
                </p>
              </div>
              <WebhookLogs logs={logs} loading={logsLoading} />
            </div>
          )}
        </>
      )}
    </Card>
  );
}

// ── Live activity feed ────────────────────────────────────────────────────────

function LiveFeed({ events }: { events: ActivityLogEntry[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  return (
    <div className="overflow-hidden flex flex-col" style={{ maxHeight: 420, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '0.5rem' }}>
      <div className="px-6 py-4 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2.5">
          <Dot color="var(--success)" />
          <SectionTitle title="Live Activity Feed" description="Real-time events from the pipeline WebSocket" />
        </div>
      </div>
      <div className="overflow-y-auto flex-1">
        {events.length === 0 ? (
          <div className="px-6 py-10 text-center">
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>
              Waiting for events — activity will appear here as the pipeline processes content.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {events.map((e, i) => {
                const ts = e.created_at ?? e.timestamp;
                return (
                  <tr
                    key={i}
                    style={{ borderBottom: '1px solid var(--border-subtle)', transition: 'background 0.12s' }}
                    onMouseEnter={ev => (ev.currentTarget.style.background = 'var(--surface-2)')}
                    onMouseLeave={ev => (ev.currentTarget.style.background = '')}
                  >
                    <td className="py-2.5 px-6 text-sm font-medium" style={{ color: 'var(--text)' }}>
                      {e.event_type}
                    </td>
                    <td className="py-2.5 px-6 text-sm" style={{ color: 'var(--text-2)' }}>
                      {e.service}
                    </td>
                    <td className="py-2.5 px-6 text-sm text-right" style={{ color: 'var(--text-3)' }}>
                      {relativeTime(ts)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Root Monitor page ─────────────────────────────────────────────────────────

export default function Monitor() {
  const [errors,    setErrors]    = useState<TelemetryError[]>([]);
  const [pipeline,  setPipeline]  = useState<PipelineMetrics | null>(null);
  const [activity,  setActivity]  = useState<ActivityLogEntry[]>([]);
  const [webhooks,  setWebhooks]  = useState<WebhookEndpoint[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetrySummary | null>(null);
  const [queue,     setQueue]     = useState<QueueStatus | null>(null);

  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(new Date());
  const [wsStatus, setWsStatus]     = useState<'connected' | 'disconnected' | 'error'>('disconnected');
  const [liveEvents, setLiveEvents] = useState<ActivityLogEntry[]>([]);

  // ── Data fetching ───────────────────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    const results = await Promise.allSettled([
      getMetricsErrors(50),
      getPipelineMetrics(),
      getActivityLog(24, 30),
      listWebhooks(),
      getTelemetrySummary(24),
      getQueueStatus(),
    ]);

    if (results[0].status === 'fulfilled') setErrors(results[0].value);
    if (results[1].status === 'fulfilled') setPipeline(results[1].value);
    if (results[2].status === 'fulfilled') setActivity(results[2].value);
    if (results[3].status === 'fulfilled') setWebhooks(results[3].value);
    if (results[4].status === 'fulfilled') setTelemetry(results[4].value);
    if (results[5].status === 'fulfilled') setQueue(results[5].value);

    setLastUpdated(new Date());
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchAll().finally(() => setLoading(false));
  }, [fetchAll]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchAll();
    setRefreshing(false);
  }, [fetchAll]);

  // ── WebSocket live feed ─────────────────────────────────────────────────────

  useEffect(() => {
    const token = localStorage.getItem('access_token') ?? '';
    if (!token) return;

    const feed = new ActivityFeedSocket(token);

    const unsubStatus  = feed.onStatus(setWsStatus);
    const unsubMessage = feed.onMessage(evt => {
      // WsActivityEvent has the same shape as ActivityLogEntry for display purposes
      setLiveEvents(prev => [evt as unknown as ActivityLogEntry, ...prev].slice(0, 100));
    });

    feed.connect();

    return () => {
      unsubStatus();
      unsubMessage();
      feed.disconnect();
    };
  }, []);

  // ── Derived values ──────────────────────────────────────────────────────────

  const errorRate   = telemetry?.error_rate ?? 0;
  const totalEvents = telemetry ? Object.values(telemetry.event_counts).reduce((a, b) => a + b, 0) : 0;
  const queueDepth  = queue?.queue_depth ?? (queue as Record<string, unknown> | null)?.['depth'] as number ?? 0;
  const activeHooks = webhooks.filter(w => w.is_active !== false).length;

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <PageHeader
        lastUpdated={lastUpdated}
        onRefresh={handleRefresh}
        refreshing={refreshing}
        wsStatus={wsStatus}
      />

      <div className="flex-1 overflow-y-auto">
        <div className="px-7 py-6 space-y-7">

          {/* ── KPI row ── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Error Rate (24h)"
              value={`${(errorRate * 100).toFixed(2)}%`}
              loading={loading}
              accent={errorRate < 0.02 ? 'var(--success)' : errorRate < 0.05 ? 'var(--warning)' : 'var(--danger)'}
            />
            <StatCard
              label="Events (24h)"
              value={totalEvents.toLocaleString()}
              loading={loading}
            />
            <StatCard
              label="Queue Depth"
              value={queueDepth}
              sub={queueDepth > 100 ? 'Backlog building' : 'Healthy'}
              loading={loading}
              accent={queueDepth > 100 ? 'var(--warning)' : 'var(--text)'}
            />
            <StatCard
              label="Active Webhooks"
              value={activeHooks}
              sub={`${webhooks.length} registered`}
              loading={loading}
              accent={activeHooks > 0 ? 'var(--success)' : 'var(--text-3)'}
            />
          </div>

          {/* ── Pipeline latencies ── */}
          <PipelinePanel pipeline={pipeline} loading={loading} />

          {/* ── Error log ── */}
          <ErrorLog errors={errors} loading={loading} />

          {/* ── Webhooks ── */}
          <WebhookPanel webhooks={webhooks} loading={loading} />

          {/* ── Live feed (WS) + recent activity (REST) side by side on wide screens ── */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <LiveFeed events={liveEvents} />

            {/* REST activity log */}
            <Card className="overflow-hidden">
              <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
                <SectionTitle
                  title="Recent Activity"
                  description={activity.length > 0 ? `${activity.length} events in the last 24 hours` : 'No recent activity'}
                />
              </div>
              {loading ? (
                <div className="p-6 space-y-3">
                  {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                </div>
              ) : activity.length === 0 ? (
                <div className="px-6 py-10 text-center">
                  <p className="text-sm" style={{ color: 'var(--text-3)' }}>No activity recorded yet.</p>
                </div>
              ) : (
                <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
                  <table className="w-full text-sm">
                    <tbody>
                      {activity.slice(0, 30).map((e, i) => (
                        <tr
                          key={i}
                          style={{ borderBottom: '1px solid var(--border-subtle)', transition: 'background 0.12s' }}
                          onMouseEnter={ev => (ev.currentTarget.style.background = 'var(--surface-2)')}
                          onMouseLeave={ev => (ev.currentTarget.style.background = '')}
                        >
                          <td className="py-2.5 px-6 text-sm font-medium" style={{ color: 'var(--text)' }}>
                            {e.event_type}
                          </td>
                          <td className="py-2.5 px-6 text-sm" style={{ color: 'var(--text-2)' }}>
                            {e.service}
                          </td>
                          <td className="py-2.5 px-6 text-sm text-right" style={{ color: 'var(--text-3)' }}>
                            {relativeTime(e.created_at ?? e.timestamp)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <div className="pb-4" />
        </div>
      </div>
    </div>
  );
}
