"use client";

import {
  ArrowRight,
  FileSpreadsheet,
  FileText,
  MessageSquareText,
  Search,
  ShieldCheck,
} from "lucide-react";

import GoogleLoginButton from "@/components/google-login-button";


export default function Home() {
  return (
    <main
      className="
        relative
        min-h-screen
        overflow-hidden
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
          bg-blue-500/[0.10]
          blur-[150px]
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
          bg-indigo-500/[0.08]
          blur-[140px]
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
          bg-blue-400/[0.08]
          blur-[150px]
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
          bg-indigo-500/[0.05]
          blur-[170px]
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

function Navbar() {
  return (
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
          className="
            transition-colors
            hover:text-[var(--primary)]
          "
        >
          Features
        </a>

        <a
          href="#how-it-works"
          className="
            transition-colors
            hover:text-[var(--primary)]
          "
        >
          How it works
        </a>
      </div>


      <ThemedGoogleLoginButton />
    </nav>
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
          bg-blue-500/[0.08]
          blur-[110px]
        "
      />


      <div
        className="
          mx-auto
          max-w-4xl
          text-center
        "
      >
        <div
          className="
            mb-7
            inline-flex
            items-center
            gap-2
            rounded-full
            border
            border-[var(--border)]
            bg-[var(--surface)]
            px-3.5
            py-1.5
            text-[12px]
            font-medium
            text-[var(--text-secondary)]
            shadow-sm
            backdrop-blur-xl
          "
        >
          <span
            className="
              h-1.5
              w-1.5
              rounded-full
              bg-[var(--primary)]
              shadow-[0_0_12px_rgba(102,117,232,0.7)]
            "
          />

          AI that understands your files
        </div>


        <h1
          className="
            text-[48px]
            font-semibold
            leading-[1.02]
            tracking-[-0.055em]
            sm:text-[64px]
            lg:text-[78px]
          "
        >
          <span className="hero-title-main">
            Your documents,
          </span>

          <br />

          <span className="hero-title-accent">
            Finally make sense.
          </span>
        </h1>


        <p
          className="
            mx-auto
            mt-7
            max-w-2xl
            text-[17px]
            leading-7
            text-[var(--text-secondary)]
            sm:text-[18px]
          "
        >
          Ask questions across PDFs, Word files,
          spreadsheets, and notes. Get clear answers
          grounded in the content you actually uploaded.
        </p>


        <div
          className="
            mt-9
            flex
            flex-col
            items-center
            justify-center
            gap-3
            sm:flex-row
          "
        >
          <a
            href="#how-it-works"
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
              shadow-[0_10px_35px_rgba(79,70,229,0.20)]
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:opacity-90
              hover:shadow-[0_14px_40px_rgba(79,70,229,0.28)]
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
              flex
              h-12
              items-center
              px-1
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
            bg-blue-500/[0.07]
            blur-[130px]
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
            <div className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
            </div>

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


          <div
            className="
              grid
              min-h-[540px]
              md:grid-cols-[220px_1fr]
            "
          >
            {/* Sidebar preview */}
            <div
              className="
                hidden
                border-r
                border-[var(--border)]
                bg-[var(--background)]
                p-4
                md:block
              "
            >
              <div
                className="
                  flex
                  items-center
                  rounded-xl
                  px-2
                  py-2
                  text-[12px]
                  font-medium
                "
              >
                New chat
              </div>


              <div
                className="
                  mt-6
                  px-2
                  text-[10px]
                  font-medium
                  uppercase
                  tracking-[0.08em]
                  text-[var(--text-muted)]
                "
              >
                Recent
              </div>


              <div className="mt-2 space-y-1">
                <DemoChatRow
                  active
                  text="Technical manual"
                />

                <DemoChatRow
                  text="Project requirements"
                />

                <DemoChatRow
                  text="Research notes"
                />
              </div>
            </div>


            {/* Main preview */}
            <div
              className="
                flex
                min-w-0
                flex-col
                bg-[var(--background)]
              "
            >
              <div
                className="
                  flex
                  h-14
                  items-center
                  justify-between
                  border-b
                  border-[var(--border)]
                  px-5
                "
              >
                <p
                  className="
                    truncate
                    text-[13px]
                    font-medium
                  "
                >
                  Technical manual
                </p>


                <div
                  className="
                    flex
                    items-center
                    gap-2
                    text-[11px]
                    text-[var(--text-muted)]
                  "
                >
                  <FileText size={14} />

                  Documents
                </div>
              </div>


              <div
                className="
                  flex
                  flex-1
                  flex-col
                  justify-between
                  p-5
                  sm:p-8
                "
              >
                <div
                  className="
                    mx-auto
                    w-full
                    max-w-2xl
                    space-y-8
                  "
                >
                  {/* User message */}
                  <div className="flex justify-end">
                    <div
                      className="
                        max-w-[75%]
                        rounded-[20px]
                        rounded-br-[6px]
                        bg-[var(--surface-active)]
                        px-4
                        py-3
                        text-[13px]
                        leading-6
                        text-[var(--text-primary)]
                      "
                    >
                      What do labels 1 and 2 identify in
                      the diagram?
                    </div>
                  </div>


                  {/* AI message */}
                  <div className="flex gap-3">
                    <div
                      className="
                        mt-0.5
                        flex
                        h-7
                        w-7
                        shrink-0
                        items-center
                        justify-center
                        rounded-full
                        border
                        border-[var(--border)]
                        bg-[var(--surface)]
                        text-[9px]
                        font-semibold
                        text-[var(--primary)]
                      "
                    >
                      AI
                    </div>


                    <div className="max-w-xl">
                      <p
                        className="
                          text-[13px]
                          leading-6
                          text-[var(--text-primary)]
                        "
                      >
                        Labels 1 and 2 identify the vacuum
                        cells. Label 1 refers to the vacuum
                        cell for the large flap, while label
                        2 identifies the vacuum cell for the
                        small flap.
                      </p>


                      <button
                        type="button"
                        className="
                          mt-4
                          flex
                          items-center
                          gap-2
                          rounded-lg
                          border
                          border-[var(--border)]
                          bg-[var(--surface)]
                          px-2.5
                          py-1.5
                          text-[10px]
                          text-[var(--text-secondary)]
                          transition
                          hover:bg-[var(--surface-hover)]
                          hover:text-[var(--primary)]
                        "
                      >
                        <FileText
                          size={12}
                          className="
                            text-[var(--primary)]
                          "
                        />

                        Technical Manual · Page 24
                      </button>
                    </div>
                  </div>
                </div>


                {/* Fake composer */}
                <div
                  className="
                    mx-auto
                    mt-12
                    w-full
                    max-w-2xl
                  "
                >
                  <div
                    className="
                      flex
                      min-h-[56px]
                      items-center
                      gap-3
                      rounded-[28px]
                      border
                      border-[var(--border)]
                      bg-[var(--surface)]
                      px-4
                      shadow-sm
                    "
                  >
                    <span
                      className="
                        text-xl
                        font-light
                        text-[var(--text-muted)]
                      "
                    >
                      +
                    </span>

                    <span
                      className="
                        flex-1
                        text-[12px]
                        text-[var(--text-muted)]
                      "
                    >
                      Ask anything
                    </span>

                    <div
                      className="
                        flex
                        h-8
                        w-8
                        items-center
                        justify-center
                        rounded-full
                        bg-[var(--primary)]
                        text-white
                      "
                    >
                      <ArrowRight size={14} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
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
   DEMO CHAT ROW
========================================================= */

function DemoChatRow({
  text,
  active = false,
}: {
  text: string;
  active?: boolean;
}) {
  return (
    <div
      className={`
        truncate
        rounded-lg
        px-2
        py-2
        text-[11px]

        ${
          active
            ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
            : "text-[var(--text-secondary)]"
        }
      `}
    >
      {text}
    </div>
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
        border-y
        border-[var(--border)]
      "
    >
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
              icon={<Search size={18} />}
              title="Grounded answers"
              description="Answers are based on your uploaded documents, with relevant context retrieved automatically."
            />

            <Feature
              icon={<FileSpreadsheet size={18} />}
              title="One workspace"
              description="Work across PDFs, Word documents, spreadsheets, and text files together."
            />

            <Feature
              icon={<ShieldCheck size={18} />}
              title="Trace the source"
              description="See the document and location behind an answer instead of trusting a black box."
            />

            <Feature
              icon={<MessageSquareText size={18} />}
              title="Natural conversation"
              description="Ask follow-up questions naturally without repeatedly searching through files."
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
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
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
      <div
        className="
          mb-8
          flex
          h-9
          w-9
          items-center
          justify-center
          rounded-lg
          border
          border-[var(--border)]
          text-[var(--primary)]
        "
      >
        {icon}
      </div>

      <h3
        className="
          text-[14px]
          font-semibold
        "
      >
        {title}
      </h3>

      <p
        className="
          mt-2
          text-[13px]
          leading-6
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
        mx-auto
        max-w-6xl
        px-6
        py-28
        lg:px-8
      "
    >
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
          No complicated setup. Add your documents and
          start asking questions.
        </p>
      </div>


      <div
        className="
          relative
          mx-auto
          mt-16
          grid
          max-w-4xl
          gap-4
          md:grid-cols-3
        "
      >
        <Step
          number="01"
          title="Add your files"
          description="Upload the documents you want to work with."
        />

        <Step
          number="02"
          title="We prepare them"
          description="The system extracts, organizes, and indexes their content."
        />

        <Step
          number="03"
          title="Start asking"
          description="Ask naturally and get answers grounded in those files."
        />
      </div>
    </section>
  );
}


/* =========================================================
   STEP
========================================================= */

function Step({
  number,
  title,
  description,
}: {
  number: string;
  title: string;
  description: string;
}) {
  return (
    <div
      className="
        rounded-2xl
        border
        border-[var(--border)]
        bg-[var(--surface)]
        p-6
      "
    >
      <div
        className="
          mb-12
          flex
          items-center
          justify-between
        "
      >
        <span
          className="
            text-[11px]
            font-medium
            text-[var(--text-muted)]
          "
        >
          {number}
        </span>

        <span
          className="
            h-1.5
            w-1.5
            rounded-full
            bg-[var(--primary)]
          "
        />
      </div>

      <h3
        className="
          text-[14px]
          font-semibold
        "
      >
        {title}
      </h3>

      <p
        className="
          mt-2
          text-[13px]
          leading-6
          text-[var(--text-secondary)]
        "
      >
        {description}
      </p>
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
        px-6
        pb-28
        pt-8
        lg:px-8
      "
    >
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
            bg-blue-500/[0.18]
            blur-[110px]
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
        border-t
        border-[var(--border)]
      "
    >
      <div
        className="
          mx-auto
          flex
          max-w-7xl
          flex-col
          items-center
          gap-6
          px-6
          py-8
          sm:flex-row
          sm:justify-between
          lg:px-8
        "
      >
        <div className="flex items-center gap-3">
          <div
            className="
              flex
              h-12
              w-12
              shrink-0
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
              src="/hj-logo.png"
              alt="Hesham Jouda logo"
              className="
                h-10
                w-10
                object-contain
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

            <span
              className="
                text-sm
                font-semibold
                tracking-wide
                text-[var(--text-primary)]
              "
            >
              Hesham Jouda
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <a
            href="https://github.com/HishamFekri"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
            title="GitHub"
            className="
              flex
              h-10
              w-10
              items-center
              justify-center
              rounded-full
              border
              border-[var(--border)]
              text-[var(--text-secondary)]
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:bg-[var(--surface-hover)]
              hover:text-[var(--text-primary)]
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
              flex
              h-10
              w-10
              items-center
              justify-center
              rounded-full
              border
              border-[var(--border)]
              text-[var(--text-secondary)]
              transition-all
              duration-200
              hover:-translate-y-0.5
              hover:bg-[var(--surface-hover)]
              hover:text-[var(--text-primary)]
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
          py-5
          text-center
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