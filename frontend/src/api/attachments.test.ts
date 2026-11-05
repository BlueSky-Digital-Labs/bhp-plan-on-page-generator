import { describe, expect, it } from "vitest";
import { splitAttachments } from "./attachments";

const file = (name: string, text: string, shortened = false) =>
  `[[Attached file: ${name} | json${shortened ? " | shortened" : ""}]]\n${text}\n[[End of attached file]]`;

describe("splitAttachments", () => {
  it("separates what was typed from an attached file", () => {
    expect(splitAttachments(`Here is our idea.\n\n${file("idea.json", '{"a": 1}')}`)).toEqual({
      typed: "Here is our idea.",
      files: [{ name: "idea.json", kind: "json", shortened: false, text: '{"a": 1}' }],
    });
  });

  it("finds every file, in order, and notes the shortened ones", () => {
    const { typed, files } = splitAttachments(`${file("a.json", "one")}\n\n${file("b.json", "two", true)}`);
    expect(typed).toBe("");
    expect(files.map((f) => [f.name, f.shortened])).toEqual([
      ["a.json", false],
      ["b.json", true],
    ]);
  });

  it("gives the same answer every time it is asked, however many answers are split in between", () => {
    const answer = `Typed.\n\n${file("a.json", "one")}\n\n${file("b.json", "two")}`;
    const first = splitAttachments(answer);
    splitAttachments(`Another.\n\n${file("c.json", "three")}`);
    splitAttachments("nothing attached");
    expect(splitAttachments(answer)).toEqual(first);
    expect(splitAttachments(answer).files).toHaveLength(2);
  });

  it("leaves plain text alone", () => {
    expect(splitAttachments("  just words  ")).toEqual({ typed: "just words", files: [] });
  });

  it("does not treat an incomplete marker as a file", () => {
    const text = "[[Attached file: x.json | json]]\nno end marker";
    expect(splitAttachments(text)).toEqual({ typed: text, files: [] });
  });
});
