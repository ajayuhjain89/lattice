import { Lock } from "lucide-react";

import { Tag } from "@/components/primitives/Tag";
import { levelLabel } from "@/lib/format";

/**
 * Sensitivity is monochrome (design-system §2.1): Internal shows nothing, Confidential an
 * outlined tag with a lock, Restricted an ink-filled tag.
 */
export function SensitivityTag({ level }: { level: string }) {
  if (level === "internal") return null;
  return (
    <Tag tone={level === "restricted" ? "inverse" : "default"} icon={<Lock size={12} strokeWidth={1.5} />}>
      {levelLabel(level)}
    </Tag>
  );
}
