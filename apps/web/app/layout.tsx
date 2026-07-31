import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CiteMind · 可信科研知识库",
  description: "带原文证据的 RAG 科研知识库与论文助手",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
