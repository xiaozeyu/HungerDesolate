import { computed, onBeforeUnmount, shallowRef, watch, type Ref } from "vue";
import { describeError, type UserError } from "./commandError";

export interface QueryState<T> {
  data: T | null;
  error: UserError | null;
  loading: boolean;
  stale: boolean;
  updatedAt: number | null;
}

interface CacheEntry {
  data: unknown;
  updatedAt: number;
}

interface QueryRequest {
  epoch: number;
  promise: Promise<unknown>;
}

const cache = new Map<string, CacheEntry>();
const inFlight = new Map<string, QueryRequest>();

export const queryClient = {
  get(key: string): CacheEntry | undefined {
    return cache.get(key);
  },
  set<T>(key: string, data: T): void {
    cache.set(key, { data, updatedAt: Date.now() });
  },
  invalidate(prefix = ""): void {
    for (const key of cache.keys()) {
      if (key.startsWith(prefix)) cache.delete(key);
    }
    for (const key of inFlight.keys()) {
      if (key.startsWith(prefix)) inFlight.delete(key);
    }
  },
  clear(): void {
    cache.clear();
  },
};

export function useQuery<T>(key: Ref<string | null>, fetcher: (key: string) => Promise<T>,
  { epoch = () => 0, paused = () => false }: { epoch?: () => number; paused?: () => boolean } = {}) {
  const state = shallowRef<QueryState<T>>({ data: null, error: null, loading: false, stale: false, updatedAt: null });
  let sequence = 0;
  let disposed = false;
  let stateKey: string | null = null;

  async function run(force = false) {
    if (disposed) return;
    const queryKey = key.value;
    const generation = epoch();
    const waiting = paused();
    const token = ++sequence;
    const cached = queryKey ? cache.get(queryKey) : undefined;
    const previous = queryKey && stateKey === queryKey ? state.value : null;
    stateKey = queryKey;
    const snapshot = (cached?.data as T | undefined) ?? previous?.data ?? null;
    state.value = {
      data: snapshot,
      error: null,
      loading: !!queryKey && !waiting,
      stale: previous?.stale ?? false,
      updatedAt: cached?.updatedAt ?? previous?.updatedAt ?? null,
    };
    if (!queryKey || waiting) return;
    const current = () => !disposed && token === sequence && key.value === queryKey && epoch() === generation && !paused();
    let entry: QueryRequest | undefined;
    try {
      const shared = inFlight.get(queryKey);
      entry = !force && shared?.epoch === generation ? shared : { epoch: generation, promise: fetcher(queryKey) };
      inFlight.set(queryKey, entry);
      const data = (await entry.promise) as T;
      if (!current()) return;
      queryClient.set(queryKey, data);
      state.value = { data, error: null, loading: false, stale: false, updatedAt: Date.now() };
    } catch (cause) {
      if (!current()) return;
      state.value = { ...state.value, error: describeError(cause), loading: false, stale: snapshot !== null };
    } finally {
      // Only remove the request we used, never a newer in-flight request.
      if (entry && inFlight.get(queryKey) === entry) inFlight.delete(queryKey);
    }
  }

  watch([key, epoch, paused], ([, , waiting], previous) => {
    // A new snapshot is needed when a switch finishes, even if a pre-switch read is still in flight.
    void run(!waiting && previous?.[2] === true);
  }, { immediate: true });

  onBeforeUnmount(() => {
    disposed = true;
    sequence++;
  });

  return {
    data: computed(() => state.value.data),
    error: computed(() => state.value.error),
    loading: computed(() => state.value.loading),
    stale: computed(() => state.value.stale),
    updatedAt: computed(() => state.value.updatedAt),
    refresh: () => run(true),
  };
}
