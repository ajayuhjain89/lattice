import { DocumentGlyph, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Tag } from "@/components/primitives/Tag";
import { sourceTypeLabel } from "@/lib/format";

export function SourceTypeTag({ sourceType }: { sourceType: string }) {
  const Glyph = SOURCE_GLYPHS[sourceType as keyof typeof SOURCE_GLYPHS] ?? DocumentGlyph;
  return <Tag icon={<Glyph size={12} aria-hidden="true" focusable="false" />}>{sourceTypeLabel(sourceType)}</Tag>;
}
