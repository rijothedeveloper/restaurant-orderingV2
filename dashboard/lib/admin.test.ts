import { describe, expect, it } from "vitest";

import {
  groupRule,
  moneyInput,
  moved,
  parseAliases,
  parseMoney,
  parsePercent,
  parseWhole,
  percentInput,
  timeInput,
} from "./admin";

describe("money fields", () => {
  it("reads dollars as cents", () => {
    expect(parseMoney("10")).toBe(1000);
    expect(parseMoney("10.5")).toBe(1050);
    expect(parseMoney(" $10.99 ")).toBe(1099);
    expect(parseMoney("0.05")).toBe(5);
    expect(parseMoney("0")).toBe(0);
  });

  it("refuses what is not a price", () => {
    for (const text of ["", "abc", "-1", "1.999", "1,50", "10.", "1000000"]) {
      expect(parseMoney(text)).toBeNull();
    }
  });

  it("shows cents as dollars", () => {
    expect(moneyInput(1099)).toBe("10.99");
    expect(moneyInput(500)).toBe("5.00");
    expect(parseMoney(moneyInput(1905))).toBe(1905);
  });
});

describe("percent fields", () => {
  it("reads a percentage as basis points", () => {
    expect(parsePercent("8.75")).toBe(875);
    expect(parsePercent("9%")).toBe(900);
    expect(parsePercent("0")).toBe(0);
    expect(parsePercent("100")).toBe(10_000);
  });

  it("refuses what is not a percentage", () => {
    for (const text of ["", "8.755", "101", "-1", "tax"]) {
      expect(parsePercent(text)).toBeNull();
    }
  });

  it("shows basis points as a percentage", () => {
    expect(percentInput(875)).toBe("8.75");
    expect(percentInput(900)).toBe("9");
  });
});

describe("parseWhole", () => {
  it("takes whole numbers in range only", () => {
    expect(parseWhole("12", 0, 240)).toBe(12);
    expect(parseWhole("0", 0, 240)).toBe(0);
    expect(parseWhole("0", 1, 100)).toBeNull();
    expect(parseWhole("241", 0, 240)).toBeNull();
    expect(parseWhole("1.5", 0, 240)).toBeNull();
    expect(parseWhole("", 0, 240)).toBeNull();
  });
});

describe("parseAliases", () => {
  it("splits on commas and drops blanks and repeats", () => {
    expect(parseAliases(" coke, Cola ,, COKE,soda pop ")).toEqual([
      "coke",
      "Cola",
      "soda pop",
    ]);
    expect(parseAliases("  ")).toEqual([]);
  });
});

describe("moved", () => {
  it("swaps an entry with its neighbour", () => {
    expect(moved(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
    expect(moved(["a", "b", "c"], 2, -1)).toEqual(["a", "c", "b"]);
  });

  it("leaves the list alone at either end", () => {
    const list = ["a", "b"];
    expect(moved(list, 0, -1)).toBe(list);
    expect(moved(list, 1, 1)).toBe(list);
  });
});

describe("groupRule", () => {
  it("says what must be chosen", () => {
    expect(groupRule({ min_selections: 1, max_selections: 1 })).toBe("Required, choose 1");
    expect(groupRule({ min_selections: 1, max_selections: 3 })).toBe(
      "Required, choose 1 to 3",
    );
    expect(groupRule({ min_selections: 0, max_selections: 1 })).toBe(
      "Optional, choose one",
    );
    expect(groupRule({ min_selections: 0, max_selections: 4 })).toBe("Optional, up to 4");
  });
});

describe("timeInput", () => {
  it("cuts seconds for a time field", () => {
    expect(timeInput("09:00:00")).toBe("09:00");
    expect(timeInput("21:30")).toBe("21:30");
  });
});
