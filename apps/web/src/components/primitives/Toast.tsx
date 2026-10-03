"use client";

/**
 * Toasts (Radix): a short confirmation of something that already happened ("Link copied",
 * "Change applied"). Never the only confirmation of a write -- the page itself shows the
 * new state -- and never for errors that need action (those are StateBlock / Callout).
 * Announced politely; pauses while hovered or focused; F8 moves focus to the region.
 */
import { X } from "lucide-react";
import { Toast as T } from "radix-ui";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { Icon } from "./icons";
import styles from "./Toast.module.css";

interface ToastMessage {
  id: number;
  title: string;
  description?: string;
}

const Context = createContext<((message: Omit<ToastMessage, "id">) => void) | null>(null);

let next = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ToastMessage[]>([]);
  const notify = useCallback((message: Omit<ToastMessage, "id">) => {
    next += 1;
    const id = next;
    // At most three at once; the oldest goes first.
    setMessages((m) => [...m.slice(-2), { ...message, id }]);
  }, []);
  const remove = (id: number) => setMessages((m) => m.filter((x) => x.id !== id));
  return (
    <Context.Provider value={notify}>
      <T.Provider duration={4000} swipeDirection="down" label="Notifications">
        {children}
        {messages.map((m) => (
          <T.Root key={m.id} className={styles.toast} onOpenChange={(open) => !open && remove(m.id)} type="foreground">
            <div className={styles.body}>
              <T.Title className={styles.title}>{m.title}</T.Title>
              {m.description && <T.Description className={styles.description}>{m.description}</T.Description>}
            </div>
            <T.Close className={styles.close} aria-label="Dismiss notification">
              <Icon as={X} size={14} />
            </T.Close>
          </T.Root>
        ))}
        <T.Viewport className={styles.viewport} />
      </T.Provider>
    </Context.Provider>
  );
}

export function useToast() {
  const notify = useContext(Context);
  return useMemo(() => notify ?? (() => {}), [notify]);
}
