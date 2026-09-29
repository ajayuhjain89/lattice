import { FileText, Hash, LifeBuoy } from "lucide-react";

import { Tag } from "@/components/primitives/Tag";
import { sourceTypeLabel } from "@/lib/format";

const ICON = { doc: FileText, ticket: LifeBuoy, chat: Hash } as const;

export function SourceTypeTag({ sourceType }: { sourceType: string }) {
  const Glyph = ICON[sourceType as keyof typeof ICON] ?? FileText;
  return <Tag icon={<Glyph size={12} strokeWidth={1.5} />}>{sourceTypeLabel(sourceType)}</Tag>;
}
