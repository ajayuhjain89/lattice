import { NotAvailable } from "@/components/states/NotAvailable";

export default function NotFound() {
  return (
    <main id="main" style={{ padding: "var(--space-16) var(--gutter)" }}>
      <NotAvailable
        label="Not found"
        title="This page doesn't exist"
        body="Check the address, or go back to Ask."
        action={{ href: "/ask", label: "Go to Ask" }}
      />
    </main>
  );
}
