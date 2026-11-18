import { describe, expect, it } from "vitest";
import { fmt } from "@/lib";

describe("formatters", () => {
  it("formats latency", () => {
    expect(fmt.ms(null)).toBe("—");
    expect(fmt.ms(4.25)).toBe("4.3 ms");
    expect(fmt.ms(250)).toBe("250 ms");
    expect(fmt.ms(1530)).toBe("1.53 s");
  });
  it("formats percentages and currency", () => {
    expect(fmt.pct(0.125)).toBe("12.5%");
    expect(fmt.pct(undefined)).toBe("—");
    expect(fmt.usd(0.000123)).toBe("$0.00012");
    expect(fmt.usd(3)).toBe("$3.00");
  });
});
