import { PublicMasthead } from "@/components/public/PublicMasthead";
import styles from "@/components/public/Public.module.css";
import { NotAvailable } from "@/components/states/NotAvailable";

/** An address that matches no page: the public masthead (no session is assumed) and the one not-available state. */
export default function NotFound() {
  return (
    <>
      <PublicMasthead />
      <main id="main" tabIndex={-1} className={styles.notFound}>
        <NotAvailable
          label="Not found"
          title="This page doesn't exist"
          body="Check the address, or go back to Ask."
          action={{ href: "/ask", label: "Go to Ask" }}
        />
      </main>
    </>
  );
}
