"use client";

import { Search } from "lucide-react";
import { useDeferredValue, useId, useState } from "react";

import { useAccessDocuments } from "@/api/queries";
import type { DocumentRef } from "@/api/types";
import { Icon } from "@/components/primitives/icons";
import { connectorLabel, levelLabel } from "@/lib/format";

import styles from "./Access.module.css";

/**
 * Find a document *you* can read (GET /v1/admin/access/documents): the API searches only
 * inside your own access, so nothing you can't read can be found, granted or explained.
 */
export function DocumentSearch({
  label,
  selected,
  onSelect,
}: {
  label: string;
  selected: DocumentRef | null;
  onSelect: (doc: DocumentRef) => void;
}) {
  const [text, setText] = useState("");
  const q = useDeferredValue(text.trim());
  const query = useAccessDocuments(q);
  const id = useId();
  const items = query.data?.items ?? [];
  return (
    <div className={styles.check}>
      <label className={styles.search}>
        <span className={styles.searchIcon} aria-hidden="true">
          <Icon as={Search} size={16} />
        </span>
        <span className="visually-hidden">{label}</span>
        <input
          className={styles.searchInput}
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Title or reference, e.g. HAL-4821"
          maxLength={100}
          aria-describedby={`${id}-help`}
        />
      </label>
      <p className={styles.help} id={`${id}-help`}>
        Only documents you can read are searched.
      </p>
      {q.length >= 2 &&
        (query.isError ? (
          <p className={styles.problem} role="alert">
            Couldn&apos;t search documents. Try again.
          </p>
        ) : !query.data ? (
          <p className={styles.help} role="status">
            Searching…
          </p>
        ) : items.length === 0 ? (
          <p className={styles.help} role="status">
            No document you can read matches.
          </p>
        ) : (
          <fieldset className={styles.fieldset}>
            <legend className="visually-hidden">{label}</legend>
            <div className={styles.results}>
              {items.map((d) => (
                <label key={d.id} className={styles.result}>
                  <input type="radio" name={`${id}-doc`} checked={selected?.id === d.id} onChange={() => onSelect(d)} />
                  <DocumentLine doc={d} />
                </label>
              ))}
            </div>
          </fieldset>
        ))}
    </div>
  );
}

export function DocumentLine({ doc }: { doc: DocumentRef }) {
  return (
    <span className={styles.rowBody}>
      <span className={styles.primary}>{doc.title}</span>
      <span className={styles.secondary}>
        {[doc.display_ref, doc.source_name, connectorLabel(doc.connector_type), levelLabel(doc.classification)].filter(Boolean).join(" · ")}
      </span>
    </span>
  );
}
