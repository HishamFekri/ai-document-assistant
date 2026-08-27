"use client";

export default function GoogleLoginButton() {
  const apiUrl =
    process.env.NEXT_PUBLIC_API_URL
    ?? "http://localhost:8000";

  function signIn() {
    window.location.assign(
      `${apiUrl}/auth/google/start`
    );
  }

  return (
    <button
      type="button"
      onClick={signIn}
      className="
        inline-flex
        min-h-11
        w-full
        items-center
        justify-center
        gap-3
        rounded-full
        border
        border-neutral-300
        bg-white
        px-5
        py-2.5
        text-sm
        font-medium
        text-neutral-800
        shadow-sm
        transition
        hover:bg-neutral-50
        active:scale-[0.99]
      "
    >
      <span
        aria-hidden="true"
        className="
          flex
          h-5
          w-5
          items-center
          justify-center
          text-lg
          font-semibold
        "
      >
        G
      </span>

      Sign in with Google
    </button>
  );
}
