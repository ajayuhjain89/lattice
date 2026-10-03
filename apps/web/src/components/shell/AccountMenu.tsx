"use client";

import { ChevronDown, LogOut } from "lucide-react";

import { Icon } from "@/components/primitives/icons";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/primitives/Menu";

import styles from "./AccountMenu.module.css";
import { initials } from "./nav";
import { useWorkspace } from "./workspace-context";

/**
 * Who is signed in, where, and the way out. Everything shown comes from /v1/me; the role
 * label is a display label, not a permission. Sign out is the only account action the
 * product has, so it is the only item.
 */
export function AccountMenu({ onSignOut }: { onSignOut: () => void }) {
  const { me } = useWorkspace();
  return (
    <Menu
      label="Account"
      trigger={
        <button type="button" className={styles.trigger} aria-label={`Account: ${me.user.display_name}`}>
          <span className={styles.initials} aria-hidden="true">
            {initials(me.user.display_name)}
          </span>
          <Icon as={ChevronDown} size={14} />
        </button>
      }
    >
      <MenuLabel>
        <AccountSummary />
      </MenuLabel>
      <MenuSeparator />
      <MenuItem icon={<Icon as={LogOut} />} onSelect={onSignOut}>
        Sign out
      </MenuItem>
    </Menu>
  );
}

/** Name, e-mail, role label and organization: shared by the account menu and the phone More sheet. */
export function AccountSummary() {
  const { me } = useWorkspace();
  return (
    <span className={styles.summary}>
      <span className={styles.name}>{me.user.display_name}</span>
      <span className={styles.email}>{me.user.email}</span>
      {me.membership.role_label && <span className={styles.meta}>{me.membership.role_label}</span>}
      <span className={styles.meta}>{me.organization.name}</span>
    </span>
  );
}
