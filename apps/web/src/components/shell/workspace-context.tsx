"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { Me } from "@/api/types";
import type { WorkspaceState } from "@/lib/workspace-state";

interface Workspace {
  me: Me;
  state: WorkspaceState;
  isAdmin: boolean;
}

const Context = createContext<Workspace | null>(null);

export function WorkspaceProvider({ value, children }: { value: Workspace; children: ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(Context);
  if (!value) throw new Error("useWorkspace outside the workspace shell");
  return value;
}
