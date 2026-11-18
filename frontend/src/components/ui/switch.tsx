import * as S from "@radix-ui/react-switch";
import { cn } from "@/lib";

export function Switch({ checked, onCheckedChange, disabled, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <S.Root checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} aria-label={label}
      className={cn("relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition disabled:opacity-50", checked ? "bg-primary" : "bg-input")}>
      <S.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition data-[state=checked]:translate-x-[18px]" />
    </S.Root>
  );
}
