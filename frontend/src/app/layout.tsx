import type {
  Metadata,
} from "next";

import "./globals.css";

import Providers from "@/components/providers";


export const metadata:
  Metadata = {
    title:
      "AI Document Assistant",

    description:
      "Chat with your documents using AI and get answers grounded in your files.",
  };


const themeScript = `
(function () {
  try {
    const savedTheme =
      localStorage.getItem("theme");

    const systemDark =
      window.matchMedia(
        "(prefers-color-scheme: dark)"
      ).matches;

    const shouldUseDark =
      savedTheme === "dark"
      || (
        (
          savedTheme === null
          || savedTheme === "system"
        )
        && systemDark
      );

    document.documentElement.classList.toggle(
      "dark",
      shouldUseDark
    );
  } catch (error) {}
})();
`;


export default function RootLayout({
  children,
}: Readonly<{
  children:
    React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html:
              themeScript,
          }}
        />
      </head>

      <body>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}