import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealTeam · 家庭治疗练习室",
  description: "在家庭对话中练习干预，观察回应，保存每一次练习与流程复盘。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body><a className="skip-link" href="#main-content">跳到主要内容</a>{children}</body>
    </html>
  );
}
