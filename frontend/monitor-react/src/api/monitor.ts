/**
 * monitor.ts
 *
 * API calls used exclusively by the Monitor page.
 * All other monitor-adjacent calls (errors, pipeline, activity, webhooks)
 * already exist in dashboard.ts and auth.ts — re-exported here for a single
 * import surface in Monitor.tsx.
 */

export {
  getMetricsErrors,
  getPipelineMetrics,
  getActivityLog,
} from './dashboard';

export {
  listWebhooks,
  getWebhookLogs,
} from './auth';

export {
  getTelemetrySummary,
  getQueueStatus,
} from './telemetry';
