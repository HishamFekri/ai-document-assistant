"use client";

import {
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";

import {
  ArrowRight,
  Moon,
  Sun,
  X,
} from "lucide-react";

import GoogleLoginButton from "@/components/google-login-button";
import { googleLoginMessage, withoutGoogleLogin } from "@/lib/login-feedback";


export default function Home() {
  return (
    <main
      className="
        landing-page
        relative
        min-h-screen
        overflow-clip
        bg-[var(--background)]
        text-[var(--text-primary)]
        transition-colors
        duration-200
      "
    >
      <LandingAmbient />

      <div className="relative z-10">
        <Navbar />
        <Hero />
        <ProductPreview />
        <Features />
        <HowItWorks />
        <FinalCTA />
        <Footer />
      </div>
    </main>
  );
}


function LoginNotice() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      const found = googleLoginMessage(window.location.search);
      if (!found) return;
      setMessage(found);
      // Drop the reason from the address bar so a refresh does not repeat it.
      window.history.replaceState(window.history.state, "", withoutGoogleLogin(window.location.href));
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

  if (!message) {
    return null;
  }

  return (
    <div
      role="alert"
      className="
        mx-auto
        mb-6
        flex
        max-w-md
        items-center
        gap-3
        rounded-2xl
        border
        border-[var(--border)]
        bg-[var(--surface)]
        px-4
        py-3
        text-left
        text-[13px]
        text-[var(--text-secondary)]
        shadow-sm
        backdrop-blur-xl
      "
    >
      <span className="flex-1">
        {message}
      </span>

      <button
        type="button"
        onClick={() => setMessage(null)}
        aria-label="Dismiss sign-in message"
        className="
          shrink-0
          rounded-full
          p-1
          text-[var(--text-muted)]
          transition-colors
          hover:bg-[var(--surface-hover)]
          hover:text-[var(--text-primary)]
        "
      >
        <X size={14} />
      </button>
    </div>
  );
}


/* =========================================================
   BACKGROUND
========================================================= */

function LandingAmbient() {
  return (
    <div
      aria-hidden="true"
      className="
        pointer-events-none
        absolute
        inset-0
        overflow-hidden
      "
    >
      {/* Hero center glow */}
      <div
        className="
          absolute
          left-1/2
          top-[200px]
          h-[560px]
          w-[900px]
          -translate-x-1/2
          rounded-full
          landing-glow
          [--glow:var(--color-blue-500)]
          [--glow-alpha:0.10]
          [--glow-blur:150px]
        "
      />

      {/* Purple-blue glow */}
      <div
        className="
          absolute
          left-[12%]
          top-[420px]
          h-[430px]
          w-[430px]
          rounded-full
          landing-glow
          [--glow:var(--color-indigo-500)]
          [--glow-alpha:0.08]
          [--glow-blur:140px]
        "
      />

      {/* Right blue glow */}
      <div
        className="
          absolute
          right-[-100px]
          top-[170px]
          h-[470px]
          w-[470px]
          rounded-full
          landing-glow
          [--glow:var(--color-blue-400)]
          [--glow-alpha:0.08]
          [--glow-blur:150px]
        "
      />

      {/* Lower subtle glow */}
      <div
        className="
          absolute
          left-1/2
          top-[1050px]
          h-[500px]
          w-[900px]
          -translate-x-1/2
          rounded-full
          landing-glow
          [--glow:var(--color-indigo-500)]
          [--glow-alpha:0.05]
          [--glow-blur:170px]
        "
      />

      {/* Very soft blue wash */}
      <div
        className="
          absolute
          inset-x-0
          top-0
          h-[780px]
          bg-gradient-to-b
          from-blue-500/[0.025]
          via-transparent
          to-transparent
        "
      />
    </div>
  );
}


/* =========================================================
   SECTION LINKS
========================================================= */

// Lands the linked section in the middle of the screen rather than under the
// top edge. The hash still updates, so the address can be shared.
function scrollToSection(event: React.MouseEvent<HTMLAnchorElement>) {
  const hash = event.currentTarget.hash;
  const target = hash ? document.querySelector<HTMLElement>(hash) : null;

  if (!target) {
    return;
  }

  event.preventDefault();

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
  window.history.replaceState(window.history.state, "", hash);
}


/* =========================================================
   THEME TOGGLE
========================================================= */

// Reads the theme from the <html> class the layout script sets before paint,
// so the first client render matches the page and the server renders neither.
function subscribeToTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

function readTheme() {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function LandingThemeToggle() {
  const theme = useSyncExternalStore(subscribeToTheme, readTheme, () => null);
  const next = theme === "dark" ? "light" : "dark";

  function toggle() {
    document.documentElement.classList.toggle("dark", next === "dark");

    try {
      localStorage.setItem("theme", next);
    } catch {
      // Private mode: the choice still applies to this visit.
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={theme === "dark" ? "Light mode" : "Dark mode"}
      className="
        flex
        h-10
        w-10
        shrink-0
        items-center
        justify-center
        rounded-full
        border
        border-[var(--border)]
        bg-[var(--surface)]
        text-[var(--text-secondary)]
        shadow-sm
        transition-all
        duration-200
        hover:-translate-y-0.5
        hover:bg-[var(--surface-hover)]
        hover:text-[var(--text-primary)]
        focus-visible:outline-2
        focus-visible:outline-offset-2
        focus-visible:outline-[var(--primary)]
      "
    >
      {theme === "dark" ? (
        <Sun size={17} strokeWidth={1.8} />
      ) : (
        <Moon size={17} strokeWidth={1.8} className={theme === null ? "opacity-0" : undefined} />
      )}
    </button>
  );
}


/* =========================================================
   GOOGLE BUTTON THEME WRAPPER
========================================================= */

function ThemedGoogleLoginButton() {
  return (
    <div
      className="
        [&_button]:!border
        [&_button]:!border-[var(--border)]
        [&_button]:!bg-[var(--surface)]
        [&_button]:!text-[var(--text-primary)]
        [&_button]:!shadow-sm
        [&_button]:!transition-all
        [&_button]:!duration-200

        [&_button:hover]:!bg-[var(--surface-hover)]

        [&_button_span]:!text-inherit

        [&_a]:!border
        [&_a]:!border-[var(--border)]
        [&_a]:!bg-[var(--surface)]
        [&_a]:!text-[var(--text-primary)]
        [&_a]:!shadow-sm
        [&_a]:!transition-all
        [&_a]:!duration-200

        [&_a:hover]:!bg-[var(--surface-hover)]

        [&_a_span]:!text-inherit
      "
    >
      <GoogleLoginButton />
    </div>
  );
}


/* =========================================================
   NAVBAR
========================================================= */

type NavbarMode = "top" | "shown" | "hidden";

// "top" at the start of the page, "hidden" while scrolling down, "shown" as
// soon as the visitor scrolls back up. Small movements are ignored.
const navbarScroll = { mode: "top" as NavbarMode, lastY: 0 };

function subscribeToNavbarScroll(onChange: () => void) {
  navbarScroll.lastY = window.scrollY;

  const onScroll = () => {
    const y = window.scrollY;
    const delta = y - navbarScroll.lastY;
    let mode = navbarScroll.mode;

    if (y <= 8) {
      mode = "top";
    } else if (delta > 6) {
      mode = "hidden";
    } else if (delta < -6) {
      mode = "shown";
    }

    if (Math.abs(delta) > 6 || y <= 8) {
      navbarScroll.lastY = y;
    }

    if (mode !== navbarScroll.mode) {
      navbarScroll.mode = mode;
      onChange();
    }
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  return () => window.removeEventListener("scroll", onScroll);
}


function Navbar() {
  const mode = useSyncExternalStore(
    subscribeToNavbarScroll,
    () => navbarScroll.mode,
    () => "top" as NavbarMode
  );

  return (
    // Gets out of the way while reading down, comes back on any scroll up.
    <header
      data-mode={mode}
      className="
        landing-navbar
        sticky
        top-0
        z-50
      "
    >
      <nav
        className="
          mx-auto
          flex
          max-w-7xl
          items-center
          justify-between
          px-6
          py-6
          lg:px-8
        "
      >
        <div className="flex items-center">
          <span
            className="
              text-[15px]
              font-semibold
              tracking-[-0.02em]
            "
          >
            AI Document Assistant
          </span>
        </div>


        <div
          className="
            hidden
            items-center
            gap-8
            text-[13px]
            text-[var(--text-secondary)]
            md:flex
          "
        >
          <a
            href="#features"
            onClick={scrollToSection}
            className="
              transition-colors
              hover:text-[var(--primary)]
            "
          >
            Features
          </a>

          <a
            href="#how-it-works"
            onClick={scrollToSection}
            className="
              transition-colors
              hover:text-[var(--primary)]
            "
          >
            How it works
          </a>
        </div>


        <div className="flex items-center gap-2.5">
          <LandingThemeToggle />

          {/* Phones use the sign-in button in the hero, just below. */}
          <div className="hidden sm:block">
            <ThemedGoogleLoginButton />
          </div>
        </div>
      </nav>
    </header>
  );
}


/* =========================================================
   HERO
========================================================= */

function Hero() {
  return (
    <section
      className="
        relative
        mx-auto
        max-w-7xl
        px-6
        pb-10
        pt-24
        lg:px-8
        lg:pt-32
      "
    >
      {/* Local glow directly behind headline */}
      <div
        aria-hidden="true"
        className="
          pointer-events-none
          absolute
          left-1/2
          top-[44%]
          -z-10
          h-[340px]
          w-[760px]
          max-w-[90vw]
          -translate-x-1/2
          -translate-y-1/2
          rounded-full
          landing-glow
          [--glow:var(--color-blue-500)]
          [--glow-alpha:0.08]
          [--glow-blur:110px]
        "
      />


      <div
        className="
          mx-auto
          max-w-4xl
          text-center
        "
      >
        <LoginNotice />

        <HeroTagline />


        <h1
          className="
            font-semibold
            leading-[1.02]
            tracking-[-0.055em]
          "
        >
          <span
            className="
              hero-title-main
              block
              text-[48px]
              sm:text-[64px]
              lg:text-[78px]
            "
          >
            Your documents,
          </span>

          <span
            className="
              hero-title-accent
              mt-1
              inline-block
              whitespace-nowrap
              text-[clamp(34px,10.2vw,44px)]
              sm:text-[64px]
              lg:text-[78px]
            "
          >
            Finally make sense.
          </span>
        </h1>


        <p
          className="
            mx-auto
            mt-7
            max-w-2xl
            text-[clamp(15px,4vw,17px)]
            leading-7
            text-[var(--text-secondary)]
            sm:text-[18px]
          "
        >
          {/* Phones get a shorter version; from sm up the copy is unchanged. */}
          <span className="block sm:hidden">
            Ask questions about your documents.
          </span>

          <span className="block sm:hidden">
            Get clear answers with citations in seconds.
          </span>

          <span className="hidden sm:block">
            Ask questions across PDFs, Word files, spreadsheets, and notes.
          </span>

          <span className="hidden sm:block">
            Get clear answers grounded in the content you actually uploaded.
          </span>
        </p>


        {/* Phones: a one-column grid, so both buttons share one width. */}
        <div
          className="
            mx-auto
            mt-9
            grid
            w-fit
            gap-3
            sm:flex
            sm:w-auto
            sm:flex-row
            sm:items-center
            sm:justify-center
          "
        >
          <a
            href="#how-it-works"
            onClick={scrollToSection}
            className="
              group
              flex
              h-12
              items-center
              justify-center
              gap-2
              rounded-full
              bg-[var(--primary)]
              px-6
              text-sm
              font-medium
              text-white
              shadow-[0_10px_35px_rgba(106,85,216,0.22)]
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:opacity-90
              hover:shadow-[0_14px_40px_rgba(106,85,216,0.30)]
              active:translate-y-0
              active:scale-[0.98]
            "
          >
            See how it works

            <ArrowRight
              size={16}
              className="
                transition-transform
                duration-200
                group-hover:translate-x-0.5
              "
            />
          </a>


          <div
            className="
              grid
              h-12
              max-sm:[&_button]:!w-full
              sm:flex
              sm:items-center
              sm:px-1
            "
          >
            <ThemedGoogleLoginButton />
          </div>
        </div>


        <div
          className="
            mt-8
            flex
            flex-wrap
            items-center
            justify-center
            gap-2
            text-[11px]
            text-[var(--text-muted)]
          "
        >
          <FileBadge label="PDF" />
          <FileBadge label="DOCX" />
          <FileBadge label="XLSX" />
          <FileBadge label="TXT" />
        </div>
      </div>
    </section>
  );
}


/* =========================================================
   HERO TAGLINE
========================================================= */

const TAGLINES = [
  "AI that understands your files",
  "Answers you can trace to the page",
  "From long reads to clear summaries",
];

const LONGEST_TAGLINE = TAGLINES.reduce(
  (longest, line) => line.length > longest.length ? line : longest
);


// Types each line like a fast typist, holds it, erases it and moves on.
// People who prefer reduced motion see the first line, still.
function HeroTagline() {
  const [text, setText] = useState("");

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const timeoutId = window.setTimeout(() => setText(TAGLINES[0]), 0);
      return () => window.clearTimeout(timeoutId);
    }

    let line = 0;
    let length = 0;
    let erasing = false;
    let timeoutId = 0;

    const step = () => {
      const target = TAGLINES[line];
      let delay: number;

      if (!erasing) {
        length += 1;
        // Slightly uneven keystrokes read as typing rather than a ticker.
        delay = length < target.length ? 38 + Math.random() * 34 : 2200;
        erasing = length >= target.length;
      } else {
        length -= 1;
        delay = 22;

        if (length === 0) {
          erasing = false;
          line = (line + 1) % TAGLINES.length;
          delay = 420;
        }
      }

      setText(target.slice(0, length));
      timeoutId = window.setTimeout(step, delay);
    };

    timeoutId = window.setTimeout(step, 500);
    return () => window.clearTimeout(timeoutId);
  }, []);

  return (
    <div
      className="
        mb-7
        inline-flex
        items-center
        text-[13px]
        font-medium
        text-[var(--text-secondary)]
      "
    >
      <span className="sr-only">{TAGLINES[0]}</span>

      {/* The longest line reserves the width, so nothing shifts while typing. */}
      <span
        aria-hidden="true"
        className="
          grid
          text-left
          whitespace-nowrap
        "
      >
        <span className="invisible col-start-1 row-start-1">
          {LONGEST_TAGLINE}
          <span className="landing-caret" />
        </span>

        <span className="col-start-1 row-start-1">
          {text}
          <span className="landing-caret" />
        </span>
      </span>
    </div>
  );
}


/* =========================================================
   FILE BADGE
========================================================= */

function FileBadge({
  label,
}: {
  label: string;
}) {
  return (
    <span
      className="
        rounded-md
        border
        border-[var(--border)]
        bg-[var(--surface)]
        px-2
        py-1
        backdrop-blur-xl
      "
    >
      {label}
    </span>
  );
}


/* =========================================================
   PRODUCT PREVIEW
========================================================= */

function ProductPreview() {
  return (
    <section
      className="
        mx-auto
        max-w-6xl
        px-4
        pb-28
        pt-16
        sm:px-6
        lg:px-8
      "
    >
      <div
        className="
          relative
          mx-auto
          max-w-5xl
        "
      >
        {/* Glow behind preview */}
        <div
          aria-hidden="true"
          className="
            pointer-events-none
            absolute
            left-1/2
            top-[45%]
            -z-10
            h-[420px]
            w-[80%]
            -translate-x-1/2
            -translate-y-1/2
            rounded-full
            landing-glow
            [--glow:var(--color-blue-500)]
            [--glow-alpha:0.07]
            [--glow-blur:130px]
          "
        />

        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          right-[-8%]
          top-[8%]
          h-[300px]
          w-[380px]
          landing-glow
          [--glow:var(--color-violet-500)]
          [--glow-alpha:0.13]
          [--glow-blur:120px]
          "
        />

        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          bottom-[-6%]
          left-[-6%]
          h-[260px]
          w-[340px]
          landing-glow
          [--glow:var(--color-indigo-500)]
          [--glow-alpha:0.11]
          [--glow-blur:120px]
          "
        />


        <div
          className="
            overflow-hidden
            rounded-[28px]
            border
            border-[var(--border)]
            bg-[var(--surface)]
            shadow-[0_30px_100px_rgba(0,0,0,0.16)]
            backdrop-blur-xl
          "
        >
          <div
            className="
              relative
              flex
              h-12
              items-center
              border-b
              border-[var(--border)]
              px-5
            "
          >
            <span
              className="
                absolute
                left-1/2
                -translate-x-1/2
                text-[11px]
                text-[var(--text-muted)]
              "
            >
              AI Document Assistant
            </span>
          </div>


          {/* The real Aura workspace, captured with demo data. */}
          <AuraScreenshot theme="light" />
          <AuraScreenshot theme="dark" />
        </div>


        <p
          className="
            mt-5
            text-center
            text-[11px]
            text-[var(--text-muted)]
          "
        >
          A focused workspace for understanding your
          documents.
        </p>
      </div>
    </section>
  );
}


/* =========================================================
   AURA SCREENSHOT
========================================================= */

// One picture per theme; the inactive one is hidden, and lazy loading means
// it is never downloaded. Phones get a portrait capture of the same chat.
function AuraScreenshot({
  theme,
}: {
  theme: "light" | "dark";
}) {
  return (
    <picture className={`landing-shot landing-shot-${theme}`}>
      <source
        media="(max-width: 639px)"
        srcSet={`/landing/aura-mobile-${theme}.webp`}
        width={390}
        height={960}
      />

      <img
        src={`/landing/aura-desktop-${theme}.webp`}
        srcSet={`/landing/aura-desktop-${theme}.webp 1x, /landing/aura-desktop-${theme}@2x.webp 2x`}
        width={1280}
        height={760}
        loading="lazy"
        decoding="async"
        alt="The AI Document Assistant workspace: a chat about a supplier agreement, answered with page citations, a table and a formula."
        className="
          block
          h-auto
          w-full
        "
      />
    </picture>
  );
}


/* =========================================================
   FEATURES
========================================================= */

function Features() {
  return (
    <section
      id="features"
      className="
        relative
        border-y
        border-[var(--border)]
      "
    >
        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          left-[4%]
          top-[18%]
          h-[360px]
          w-[460px]
          landing-glow
          [--glow:var(--color-indigo-500)]
          [--glow-alpha:0.13]
          [--glow-blur:140px]
          "
        />

        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          right-[2%]
          top-[30%]
          h-[460px]
          w-[560px]
          landing-glow
          [--glow:var(--color-violet-500)]
          [--glow-alpha:0.15]
          [--glow-blur:150px]
          "
        />

      <div
        className="
          mx-auto
          max-w-6xl
          px-6
          py-28
          lg:px-8
        "
      >
        <div
          className="
            grid
            gap-12
            lg:grid-cols-[0.8fr_1.2fr]
            lg:gap-20
          "
        >
          <div>
            <span
              className="
                text-[11px]
                font-semibold
                uppercase
                tracking-[0.12em]
                text-[var(--primary)]
              "
            >
              Built for clarity
            </span>


            <h2
              className="
                mt-4
                max-w-md
                text-3xl
                font-semibold
                leading-tight
                tracking-[-0.035em]
                sm:text-4xl
              "
            >
              Less searching.
              <br />
              More understanding.
            </h2>


            <p
              className="
                mt-5
                max-w-md
                text-[15px]
                leading-7
                text-balance
                text-[var(--text-secondary)]
              "
            >
              Your files become one searchable knowledge
              space, so you can spend less time finding
              information and more time using it.
            </p>
          </div>


          <div
            className="
              grid
              gap-px
              overflow-hidden
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--border)]
              sm:grid-cols-2
            "
          >
            <Feature
              title="AI document chat"
              description="Ask questions about your files and get clear answers, with citations that point to the source."
            />

            <Feature
              title="Smart summaries"
              description="Turn long documents into organized summaries that bring the important points forward."
            />

            <Feature
              title="Document transcription"
              description="Read a document's extracted content page by page, in a clean, structured layout."
            />

            <Feature
              title="Images, tables & equations"
              description="Figures, tables and math appear properly formatted in answers and summaries, where the file includes them."
            />

            <Feature
              title="Multiple file formats"
              description="Upload PDF, Word (DOCX), Excel (XLSX) and text files, and work across them together."
            />

            <Feature
              title="Organized conversations"
              description="Pin the chats that matter, archive the rest, and pick up any conversation where you left off."
            />
          </div>
        </div>
      </div>
    </section>
  );
}


/* =========================================================
   FEATURE
========================================================= */

function Feature({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div
      className="
        bg-[var(--background)]
        p-7
        transition-colors
        duration-200
        hover:bg-[var(--surface)]
      "
    >
      <h3
        className="
          text-[15px]
          font-semibold
          tracking-[-0.01em]
        "
      >
        {title}
      </h3>

      <p
        className="
          mt-2
          text-[13px]
          leading-6
          text-pretty
          text-[var(--text-secondary)]
        "
      >
        {description}
      </p>
    </div>
  );
}


/* =========================================================
   HOW IT WORKS
========================================================= */

function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="
        relative
        mx-auto
        max-w-6xl
        px-6
        py-28
        lg:px-8
      "
    >
        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          left-1/2
          top-[52%]
          h-[360px]
          w-[620px]
          max-w-[90vw]
          -translate-x-1/2
          landing-glow
          [--glow:var(--color-violet-500)]
          [--glow-alpha:0.15]
          [--glow-blur:140px]
          "
        />

        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          right-[-4%]
          top-[40%]
          h-[300px]
          w-[360px]
          landing-glow
          [--glow:var(--color-blue-500)]
          [--glow-alpha:0.12]
          [--glow-blur:130px]
          "
        />

      <div
        className="
          mx-auto
          max-w-2xl
          text-center
        "
      >
        <span
          className="
            text-[11px]
            font-semibold
            uppercase
            tracking-[0.12em]
            text-[var(--primary)]
          "
        >
          Simple by design
        </span>

        <h2
          className="
            mt-4
            text-3xl
            font-semibold
            tracking-[-0.035em]
            sm:text-4xl
          "
        >
          From file to answer.
        </h2>

        <p
          className="
            mt-4
            text-[15px]
            leading-7
            text-[var(--text-secondary)]
          "
        >
          <span className="block sm:inline">No complicated setup.</span>{" "}
          <span className="block sm:inline">Add your documents and start asking questions.</span>
        </p>
      </div>


      <ol
        className="
          mx-auto
          mt-16
          grid
          max-w-5xl
          gap-14
          md:grid-cols-3
          md:gap-x-10
          md:gap-y-0
        "
      >
        <Step
          number="01"
          title="Add your files"
          hideSceneOnPhones
          description="Upload PDFs, Word documents, spreadsheets or text files. Work with one file or several at once."
        >
          <div className="space-y-2">
            <FileLine name="Supplier Agreement 2026" type="PDF" />
            <FileLine name="Regional Revenue Q3" type="XLSX" />
            <FileLine name="Product Research Notes" type="DOCX" />
          </div>
        </Step>

        <Step
          number="02"
          title="Every page is read"
          description="Text, tables, images and equations are extracted and indexed, so the details are there when you ask."
        >
          <div
            className="
              rounded-xl
              border
              border-[var(--border)]
              bg-[var(--surface)]
              px-4
              py-3.5
            "
          >
            <div
              className="
                flex
                items-baseline
                justify-between
                gap-3
                text-[12px]
              "
            >
              <span className="truncate font-medium">
                Supplier Agreement 2026
              </span>

              <span className="shrink-0 text-[var(--text-muted)]">
                30 of 42 pages
              </span>
            </div>

            <div
              className="
                mt-3
                h-1
                overflow-hidden
                rounded-full
                bg-[var(--surface-active)]
              "
            >
              <div className="landing-progress h-full w-[71%] rounded-full bg-[var(--primary)]" />
            </div>
          </div>
        </Step>

        <Step
          number="03"
          title="Ask and check"
          description="Ask in plain language. Each answer cites the page it came from, so you can verify it in seconds."
        >
          <div className="space-y-2.5 text-[12px] leading-5">
            <p
              className="
                ml-auto
                w-fit
                max-w-[85%]
                rounded-2xl
                rounded-br-md
                bg-[var(--surface-active)]
                px-3.5
                py-2
              "
            >
              When can we end the contract?
            </p>

            <p className="pr-4 text-[var(--text-secondary)]">
              Either party can end it with 90 days&apos; notice.{" "}
              <span
                className="
                  whitespace-nowrap
                  rounded-md
                  bg-[var(--primary-soft)]
                  px-1.5
                  py-0.5
                  text-[11px]
                  font-medium
                  text-[var(--primary)]
                "
              >
                p. 18
              </span>
            </p>
          </div>
        </Step>
      </ol>
    </section>
  );
}


/* =========================================================
   STEP
========================================================= */

// An open step: a small scene from the product, then a numbered caption under
// a hairline. No card, so the three read as one flow.
function Step({
  number,
  title,
  description,
  hideSceneOnPhones = false,
  children,
}: {
  number: string;
  title: string;
  description: string;
  hideSceneOnPhones?: boolean;
  children: React.ReactNode;
}) {
  return (
    // Subgrid rows line the scenes and captions up across the three steps.
    <li className="grid grid-rows-[auto_auto] md:row-span-2 md:grid-rows-subgrid">
      <div
        aria-hidden="true"
        className={`
          flex
          flex-col
          justify-end
          pb-7
          ${hideSceneOnPhones ? "max-sm:hidden" : ""}
        `}
      >
        {children}
      </div>

      <div
        className="
          border-t
          border-[var(--border)]
          pt-6
        "
      >
        <div className="flex items-baseline gap-3">
          <span
            className="
              text-[13px]
              font-semibold
              tabular-nums
              text-[var(--primary)]
            "
          >
            {number}
          </span>

          <h3
            className="
              text-[16px]
              font-semibold
              tracking-[-0.01em]
            "
          >
            {title}
          </h3>
        </div>

        <p
          className="
            mt-3
            text-[14px]
            leading-6
            text-pretty
            text-[var(--text-secondary)]
          "
        >
          {description}
        </p>
      </div>
    </li>
  );
}


function FileLine({
  name,
  type,
}: {
  name: string;
  type: string;
}) {
  return (
    <div
      className="
        flex
        items-center
        justify-between
        gap-3
        rounded-xl
        border
        border-[var(--border)]
        bg-[var(--surface)]
        px-3.5
        py-2.5
        text-[12px]
      "
    >
      <span className="truncate font-medium">
        {name}
      </span>

      <span
        className="
          shrink-0
          text-[10px]
          font-semibold
          tracking-[0.06em]
          text-[var(--text-muted)]
        "
      >
        {type}
      </span>
    </div>
  );
}


/* =========================================================
   FINAL CTA
========================================================= */

function FinalCTA() {
  return (
    <section
      className="
        relative
        px-6
        pb-28
        pt-8
        lg:px-8
      "
    >
        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          left-1/2
          top-[45%]
          h-[380px]
          w-[760px]
          max-w-[95vw]
          -translate-x-1/2
          -translate-y-1/2
          landing-glow
          [--glow:var(--color-indigo-500)]
          [--glow-alpha:0.13]
          [--glow-blur:150px]
          "
        />

      <div
        className="
          relative
          mx-auto
          max-w-5xl
          overflow-hidden
          rounded-[32px]
          border
          border-[var(--border)]
          bg-[var(--surface)]
          px-6
          py-20
          text-center
          shadow-2xl
          shadow-black/10
          sm:px-12
        "
      >
        <div
          aria-hidden="true"
          className="
            pointer-events-none
            absolute
            left-1/2
            top-0
            h-64
            w-[650px]
            max-w-[100vw]
            -translate-x-1/2
            -translate-y-1/2
            rounded-full
            landing-glow
            [--glow:var(--color-blue-500)]
            [--glow-alpha:0.18]
            [--glow-blur:110px]
          "
        />


        <h2
          className="
            relative
            mx-auto
            max-w-xl
            text-3xl
            font-semibold
            tracking-[-0.035em]
            sm:text-4xl
          "
        >
          Stop digging through documents.
        </h2>

        <p
          className="
            relative
            mx-auto
            mt-4
            max-w-lg
            text-[14px]
            leading-6
            text-[var(--text-secondary)]
          "
        >
          Turn your files into a workspace you can
          actually have a conversation with.
        </p>

        <div
          className="
            relative
            mt-8
            flex
            justify-center
          "
        >
          <ThemedGoogleLoginButton />
        </div>
      </div>
    </section>
  );
}


/* =========================================================
   FOOTER
========================================================= */

function Footer() {
  return (
    <footer
      className="
        relative
        border-t
        border-[var(--border)]
      "
    >
        <div
          aria-hidden="true"
          className="
          pointer-events-none
          absolute
          -z-10
          rounded-full
          bottom-[-40%]
          left-[8%]
          h-[260px]
          w-[420px]
          landing-glow
          [--glow:var(--color-violet-500)]
          [--glow-alpha:0.12]
          [--glow-blur:130px]
          "
        />

      <div
        className="
          mx-auto
          flex
          max-w-7xl
          items-center
          justify-between
          gap-3
          px-6
          py-4
          sm:gap-6
          sm:py-8
          lg:px-8
        "
      >
        {/* Phones: one compact row (logo, name, icons). From sm up: unchanged. */}
        <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
          <div
            className="
              flex
              h-10
              w-10
              shrink-0
              sm:h-12
              sm:w-12
              items-center
              justify-center
              overflow-hidden
              rounded-xl
              bg-[#080808]
              ring-1
              ring-white/10
            "
          >
            <img
              src="/hj-logo-120.webp"
              alt="Hisham Jouda logo"
              width={40}
              height={40}
              loading="lazy"
              decoding="async"
              className="
                h-8
                w-8
                object-contain
                sm:h-10
                sm:w-10
              "
            />
          </div>

          <div className="flex flex-col leading-tight">
            <span
              className="
                mb-0.5
                text-[10px]
                font-medium
                uppercase
                tracking-[0.18em]
                text-[var(--text-muted)]
              "
            >
              by
            </span>

            <a
              href="https://hishamjouda.com"
              target="_blank"
              rel="noopener noreferrer"
              className="
                landing-name-link
                self-start
                text-[var(--text-primary)]
              "
            >
              {/* Typography stays on the span: the global `a { font: inherit }` reset would override it. */}
              <span
                className="
                  text-sm
                  font-semibold
                  tracking-wide
                "
              >
                Hisham Jouda
              </span>

              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <a
            href="https://github.com/HishamFekri"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
            title="GitHub"
            className="
              brand-github
              flex
              h-9
              w-9
              items-center
              justify-center
              rounded-full
              border
              border-[var(--border)]
              sm:h-10
              sm:w-10
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:bg-[var(--surface-hover)]
            "
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M12 .7a11.3 11.3 0 0 0-3.57 22.02c.56.1.77-.24.77-.54v-2.1c-3.12.68-3.78-1.32-3.78-1.32-.51-1.3-1.25-1.65-1.25-1.65-1.02-.7.08-.68.08-.68 1.13.08 1.72 1.16 1.72 1.16 1 1.72 2.63 1.22 3.27.93.1-.73.39-1.22.71-1.5-2.49-.28-5.11-1.25-5.11-5.57 0-1.23.44-2.24 1.16-3.03-.12-.28-.5-1.43.11-2.98 0 0 .95-.3 3.11 1.16A10.8 10.8 0 0 1 12 6.2c.96 0 1.93.13 2.83.38 2.16-1.46 3.1-1.16 3.1-1.16.62 1.55.23 2.7.12 2.98.72.79 1.16 1.8 1.16 3.03 0 4.33-2.63 5.28-5.13 5.56.4.35.76 1.04.76 2.1v3.1c0 .3.2.65.78.54A11.3 11.3 0 0 0 12 .7Z" />
            </svg>
          </a>

          <a
            href="https://www.linkedin.com/in/hishamjouda"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="LinkedIn"
            title="LinkedIn"
            className="
              brand-linkedin
              flex
              h-9
              w-9
              items-center
              justify-center
              rounded-full
              border
              border-[var(--border)]
              sm:h-10
              sm:w-10
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:bg-[var(--surface-hover)]
            "
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.34V8.98h3.42v1.57h.05c.47-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.29ZM5.33 7.41a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12ZM7.11 20.45H3.55V8.98h3.56v11.47Z" />
            </svg>
          </a>
        </div>
      </div>

      <div
        className="
          mx-auto
          max-w-7xl
          border-t
          border-[var(--border)]
          px-6
          py-4
          text-center
          sm:py-5
          text-[11px]
          text-[var(--text-muted)]
          lg:px-8
        "
      >
        © 2026 AI Document Assistant
      </div>
    </footer>
  );
}