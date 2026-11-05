import { describe, expect, it } from "vitest";
import { looksLikeAddress, parseRecipients } from "./email";

describe("parseRecipients", () => {
  it("splits on commas, semicolons, spaces and new lines, and drops empty parts", () => {
    expect(parseRecipients("a@x.com, b@x.com;c@x.com  d@x.com\ne@x.com,,")).toEqual([
      "a@x.com",
      "b@x.com",
      "c@x.com",
      "d@x.com",
      "e@x.com",
    ]);
  });

  it("drops repeats whatever the case and keeps the first spelling", () => {
    expect(parseRecipients("A@x.com a@X.COM b@x.com")).toEqual(["A@x.com", "b@x.com"]);
  });

  it("gives nothing for blank text", () => {
    expect(parseRecipients("  , ; \n")).toEqual([]);
  });
});

describe("looksLikeAddress", () => {
  it.each(["a@example.com", "first.last+tag@sub.example.co.uk"])("accepts %s", (value) => {
    expect(looksLikeAddress(value)).toBe(true);
  });
  it.each(["plain", "a@b", "a@@b.com", "Bob<b@x.com>", '"a"@x.com', "a@x.c"])("rejects %s", (value) => {
    expect(looksLikeAddress(value)).toBe(false);
  });
});
