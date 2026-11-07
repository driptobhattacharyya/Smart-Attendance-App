import React, { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { InlineSpan, parseMarkdown } from "@/lib/markdown";

/** Renders the small Markdown subset from lib/markdown.ts with the app's text colours. */
export default function MarkdownText({ source }: { source: string }) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);

  const renderSpans = (spans: InlineSpan[]) =>
    spans.map((span, i) => (
      <Text key={i} style={span.bold ? styles.bold : undefined}>
        {span.text}
      </Text>
    ));

  return (
    <View>
      {blocks.map((block, i) => {
        switch (block.type) {
          case "h1":
            return (
              <Text key={i} style={styles.h1}>
                {renderSpans(block.spans)}
              </Text>
            );
          case "h2":
            return (
              <Text key={i} style={styles.h2}>
                {renderSpans(block.spans)}
              </Text>
            );
          case "li":
            return (
              <View key={i} style={styles.listItem}>
                <Text style={styles.bullet}>{"•"}</Text>
                <Text style={styles.body}>{renderSpans(block.spans)}</Text>
              </View>
            );
          default:
            return (
              <Text key={i} style={styles.body}>
                {renderSpans(block.spans)}
              </Text>
            );
        }
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 24, fontWeight: "bold", color: "#5B3B8E", marginBottom: 12 },
  h2: { fontSize: 19, fontWeight: "bold", color: "#5B3B8E", marginTop: 16, marginBottom: 8 },
  body: { fontSize: 16, lineHeight: 24, color: "#4A3B94", marginBottom: 10 },
  bold: { fontWeight: "bold" },
  listItem: { flexDirection: "row", paddingLeft: 4, marginBottom: 6 },
  bullet: { fontSize: 16, lineHeight: 24, color: "#4A3B94", width: 18 },
});
