import type {
  Metadata,
} from "next";

import {
  Geist,
} from "next/font/google";

import localFont from "next/font/local";

import "./globals.css";

import Providers from "@/components/providers";
import BackendWarmup from "@/components/chat/BackendWarmup";


// Exposed as a CSS variable only; the chat workspace opts in through `.aura`.
const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});


// Arabic text everywhere (Noto Sans Arabic, SIL OFL, see fonts/OFL.txt). Placed
// first in each font stack but limited by unicode-range to Arabic characters, so
// Latin text keeps its font and pages without Arabic never download it. Static
// files remap weights: regular text renders Medium (500), medium and heavier
// render SemiBold (600).
const arabic = localFont({
  src: [
    { path: "./fonts/NotoSansArabic-Medium-arabic.woff2", weight: "100 450", style: "normal" },
    { path: "./fonts/NotoSansArabic-SemiBold-arabic.woff2", weight: "451 900", style: "normal" },
  ],
  variable: "--font-arabic",
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  declarations: [{
    prop: "unicode-range",
    value: "U+0600-06FF, U+0750-077F, U+0870-0891, U+0897-08E1, U+08E3-08FF, U+200C-200E, U+204F, U+2E41, U+FB50-FDFF, U+FE70-FE74, U+FE76-FEFC",
  }],
});


export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL
    || (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : "http://localhost:3000")),
  title:
    "AI Document Assistant",

  description:
    "Chat with your documents using AI and get answers grounded in your files.",

  openGraph: {
    title:
      "AI Document Assistant",

    description:
      "Chat with your documents using AI and get answers grounded in your files.",

    type:
      "website",

    images: [
      {
        url:
          "/og-image.jpeg",

        width:
          1200,

        height:
          630,

        alt:
          "AI Document Assistant",
      },
    ],
  },

  twitter: {
    card:
      "summary_large_image",

    title:
      "AI Document Assistant",

    description:
      "Chat with your documents using AI and get answers grounded in your files.",

    images: [
      "/og-image.jpeg",
    ],
  },
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
        <link
          rel="preconnect"
          href="https://accounts.google.com"
        />

        <link
          rel="preconnect"
          href="https://accounts.gstatic.com"
          crossOrigin="anonymous"
        />

        <link
          rel="preconnect"
          href="https://www.gstatic.com"
          crossOrigin="anonymous"
        />

        <link
          rel="dns-prefetch"
          href="https://accounts.google.com"
        />

        <link
          rel="dns-prefetch"
          href="https://accounts.gstatic.com"
        />

        <link
          rel="dns-prefetch"
          href="https://www.gstatic.com"
        />

        <script
          dangerouslySetInnerHTML={{
            __html:
              themeScript,
          }}
        />
      </head>

      <body className={`${geist.variable} ${arabic.variable}`}>
        <Providers>
          <BackendWarmup />

          {children}
        </Providers>
      </body>
    </html>
  );
}