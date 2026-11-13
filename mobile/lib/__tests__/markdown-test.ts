import { parseInline, parseMarkdown } from "../markdown";

describe("parseInline", () => {
  it("returns one plain span for text without bold", () => {
    expect(parseInline("plain text")).toEqual([{ text: "plain text", bold: false }]);
  });

  it("splits **bold** from plain text", () => {
    expect(parseInline("We collect **location** only.")).toEqual([
      { text: "We collect ", bold: false },
      { text: "location", bold: true },
      { text: " only.", bold: false },
    ]);
  });

  it("keeps an unclosed ** as literal text", () => {
    expect(parseInline("price **5")).toEqual([{ text: "price **5", bold: false }]);
  });
});

describe("parseMarkdown", () => {
  it("parses headings, paragraphs and bullet lists", () => {
    const source = [
      "# Privacy notice",
      "",
      "## What we collect",
      "Location is collected",
      "while you are checked in.",
      "",
      "- Location",
      "* **Beacon** signal",
    ].join("\n");

    expect(parseMarkdown(source)).toEqual([
      { type: "h1", spans: [{ text: "Privacy notice", bold: false }] },
      { type: "h2", spans: [{ text: "What we collect", bold: false }] },
      {
        type: "p",
        spans: [{ text: "Location is collected while you are checked in.", bold: false }],
      },
      { type: "li", spans: [{ text: "Location", bold: false }] },
      { type: "li", spans: [{ text: "Beacon", bold: true }, { text: " signal", bold: false }] },
    ]);
  });

  it("handles CRLF line endings and extra blank lines", () => {
    const blocks = parseMarkdown("# A\r\n\r\n\r\nText\r\n");
    expect(blocks.map((b) => b.type)).toEqual(["h1", "p"]);
  });

  it("returns no blocks for empty input", () => {
    expect(parseMarkdown("   \n\n")).toEqual([]);
  });

  it("does not treat a line starting with ** as a bullet", () => {
    expect(parseMarkdown("**Note** here")).toEqual([
      { type: "p", spans: [{ text: "Note", bold: true }, { text: " here", bold: false }] },
    ]);
  });
});
