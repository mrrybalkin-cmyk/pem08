export const state = {
    competitors: [],
    competitorDetails: {},
    sourceDetails: {},
    activeCompetitorId: null,
    activeCompetitorDetail: null,
    activeSourceId: null,
    activeSourceDetail: null,
    activeAnalysis: null,
    analysisContext: '',
    analysisTab: 'overview',
    analysisHistory: [],
    analysisMode: 'source',
    compareSelection: new Set(),
    comparisonResult: null,
    loading: new Set(),
    modal: null,
    compareOpen: false,
    error: '',
    status: '',
    competitorVersion: 0,
    sourceVersion: 0,
    listVersion: 0,
};

export const orderedAnalyses = items => [...items].sort((a, b) =>
    b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
export const currentSnapshot = detail => detail?.snapshots?.at(-1) || null;
export const currentAnalyses = detail => orderedAnalyses((detail?.analyses || [])
    .filter(item => item.snapshot_id === currentSnapshot(detail)?.id));
