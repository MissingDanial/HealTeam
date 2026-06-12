import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealTeam MVP",
  description: "Family Therapy AI Simulator — W1 foundation",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-heal-50 text-heal-900 antialiased">{children}</body>
    </html>
  );
}
