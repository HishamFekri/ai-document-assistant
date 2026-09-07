"use client";

import {
  useEffect,
  useState,
} from "react";

import {
  ImageIcon,
  Loader2,
} from "lucide-react";


type Props = {
  imageUrl: string;
  alt: string;
};


const API_URL =
  process.env.NEXT_PUBLIC_API_URL
  ?? "http://localhost:8000";


export default function SourceImage({
  imageUrl,
  alt,
}: Props) {
  const [
    blobUrl,
    setBlobUrl,
  ] = useState<string | null>(
    null
  );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    failed,
    setFailed,
  ] = useState(false);


  useEffect(() => {
    let cancelled = false;

    let createdUrl:
      | string
      | null = null;


    async function loadImage() {
      try {
        setLoading(true);
        setFailed(false);

        // Accept only application image paths. Historical sources are normalized
        // by the API; never fall back to a raw origin or follow an image redirect.
        if (!/^\/documents\/[1-9]\d*\/(?:assets\/[^/?#\\]+(?:\/file)?|image-chunks\/[1-9]\d*\/file)$/.test(imageUrl)) {
          throw new Error("Invalid document image path");
        }

        const response = await fetch(
          `${API_URL.replace(/\/$/, "")}${imageUrl}`,
          {
            credentials: "include",
            cache: "no-cache",
            redirect: "error",
          }
        );

        if (!response.ok) {
          throw new Error(
            `Could not load image (${response.status})`
          );
        }

        const blob =
          await response.blob();

        if (cancelled) {
          return;
        }

        createdUrl =
          URL.createObjectURL(
            blob
          );

        setBlobUrl(
          createdUrl
        );

      } catch (error) {
        console.error(
          error
        );

        if (!cancelled) {
          setFailed(
            true
          );
        }

      } finally {
        if (!cancelled) {
          setLoading(
            false
          );
        }
      }
    }


    loadImage();


    return () => {
      cancelled = true;

      if (createdUrl) {
        URL.revokeObjectURL(
          createdUrl
        );
      }
    };
  }, [imageUrl]);


  if (loading) {
    return (
      <div className="mt-3 flex h-44 w-full items-center justify-center rounded-xl border border-neutral-200 bg-neutral-50">
        <div className="flex items-center gap-2 text-xs text-neutral-400">
          <Loader2
            size={18}
            className="animate-spin"
          />

          Loading image...
        </div>
      </div>
    );
  }


  if (
    failed
    || !blobUrl
  ) {
    return (
      <div className="mt-3 flex h-28 w-full items-center justify-center rounded-xl border border-neutral-200 bg-neutral-50">
        <div className="flex items-center gap-2 text-xs text-neutral-400">
          <ImageIcon
            size={16}
          />

          Could not load image
        </div>
      </div>
    );
  }


  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-neutral-200 bg-neutral-50">
      <img
        src={
          blobUrl
        }
        alt={
          alt
        }
        className="max-h-[520px] w-full object-contain"
      />
    </div>
  );
}
