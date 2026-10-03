"use client";

import { useDeferredValue, useId, useState } from "react";

import { useAccessDocuments } from "@/api/queries";
import type { DocumentRef } from "@/api/types";
import { ChoiceGroup, Field, Radio, SearchInput } from "@/components/primitives/Field";
import { SensitivityTag } from "@/components/evidence/SensitivityTag";
import { connectorLabel } from "@/lib/format";

import styles from "./Access.module.css";

/**
 * Find a document *you* can read (GET /v1/admin/access/documents): the API searches only
 * inside your own access, so nothing you can't read can be found, granted or explained.
 */
export function DocumentSearch({ label, selected, onSelect }: { label: string; selected: DocumentRef | null; onSelect: (doc: DocumentRef) => void }) {
  const [text, setText] = useState("");
  const q = useDeferredValue(text.trim());
  const query = useAccessDocuments(q);
  const id = useId();
  const items = query.data?.items ?? [];
  return (
    <div className={styles.docSearch}>
      <Field label={label} hideLabel help="Only documents you can read are searched.">
        {(control) => <SearchInput {...control} value={text} onValueChange={setText} placeholder="Title or reference, e.g. HAL-4821" maxLength={100} />}
      </Field>
      {q.length >= 2 &&
        (query.isError ? (
          <p className={styles.problem} role="alert">
            Couldn&apos;t search documents. Try again.
          </p>
        ) : !query.data ? (
          <p className={styles.secondary} role="status">
            Searching…
          </p>
        ) : items.length === 0 ? (
          <p className={styles.secondary} role="status">
            No document you can read matches.
          </p>
        ) : (
          <ChoiceGroup legend={label} hideLegend>
            <div className={styles.results}>
              {items.map((d) => (
                <Radio key={d.id} name={`${id}-doc`} checked={selected?.id === d.id} onChange={() => onSelect(d)} label={<DocumentLine doc={d} />} />
              ))}
            </div>
          </ChoiceGroup>
        ))}
    </div>
  );
}

export function DocumentLine({ doc }: { doc: DocumentRef }) {
  return (
    <span className={styles.doc}>
      <span className={styles.docTitle}>{doc.title}</span>
      <span className={styles.docMeta}>
        {doc.display_ref && <span className={styles.ref}>{doc.display_ref}</span>}
        <span>{doc.source_name}</span>
        <span>{connectorLabel(doc.connector_type)}</span>
        <SensitivityTag level={doc.classification} />
      </span>
    </span>
  );
}
