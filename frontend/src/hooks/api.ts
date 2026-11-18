import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorMessage, get } from "@/services/api";

type Interval = number | false | ((data: unknown) => number | false);

export function useApi<T>(key: QueryKey, path: string | null, opts: { refetchInterval?: Interval; enabled?: boolean } = {}) {
  const ri = opts.refetchInterval;
  return useQuery<T>({ queryKey: key, queryFn: () => get<T>(path!), enabled: !!path && (opts.enabled ?? true),
                       refetchInterval: typeof ri === "function" ? (q) => ri(q.state.data) : ri });
}

export function useAction<TArgs, TRes>(fn: (a: TArgs) => Promise<TRes>, o: { invalidate?: QueryKey[]; success?: string | ((r: TRes) => string); onSuccess?: (r: TRes) => void } = {}) {
  const qc = useQueryClient();
  return useMutation<TRes, Error, TArgs>({
    mutationFn: fn,
    onSuccess: (r) => {
      o.invalidate?.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      if (o.success) toast.success(typeof o.success === "function" ? o.success(r) : o.success);
      o.onSuccess?.(r);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
