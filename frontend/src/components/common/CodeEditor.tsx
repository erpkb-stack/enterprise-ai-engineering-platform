/** Monaco editor bundled locally (no CDN) with web workers via Vite `?worker` imports. */
import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/language/json/json.worker?worker";
import { useUi } from "@/stores/auth";

self.MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    return label === "json" ? new JsonWorker() : new EditorWorker();
  },
};
loader.config({ monaco });

export default function CodeEditor({ value, onChange, language = "markdown", height = 220, readOnly = false, ariaLabel }: {
  value: string; onChange?: (v: string) => void; language?: string; height?: number | string; readOnly?: boolean; ariaLabel?: string;
}) {
  const theme = useUi((s) => s.theme);
  return (
    <div className="overflow-hidden rounded-md border" aria-label={ariaLabel}>
      <Editor
        height={height}
        language={language}
        value={value}
        theme={theme === "dark" ? "vs-dark" : "light"}
        onChange={(v) => onChange?.(v ?? "")}
        options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: "on", readOnly, scrollBeyondLastLine: false,
                   lineNumbers: "on", tabSize: 2, automaticLayout: true, ariaLabel }}
      />
    </div>
  );
}
