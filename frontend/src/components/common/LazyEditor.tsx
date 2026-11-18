import { lazy, Suspense } from "react";
import { Textarea } from "@/components/ui/form";
import { Loading } from "./index";

const CodeEditor = lazy(() => import("./CodeEditor"));
type Props = { value: string; onChange?: (v: string) => void; language?: string; height?: number | string; readOnly?: boolean; ariaLabel?: string };

/** Lazily loads Monaco; under tests (jsdom) falls back to a textarea. */
export default function Editor(props: Props) {
  if (import.meta.env.MODE === "test") {
    return <Textarea aria-label={props.ariaLabel} value={props.value} onChange={(e) => props.onChange?.(e.target.value)} readOnly={props.readOnly} />;
  }
  return (
    <Suspense fallback={<Loading label="Loading editor…" />}>
      <CodeEditor {...props} />
    </Suspense>
  );
}
