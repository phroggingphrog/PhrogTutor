"use client";

import { useEffect } from "react";

import type { SessionViewerPanelHandle } from "@/components/chat/home/SessionViewerPanel";
import { useGeogebraTabOpener } from "@/context/GeogebraTabContext";
import { useQuizFollowupController } from "@/context/QuizFollowupContext";

/** Connect in-message Quiz/GeoGebra actions to a surface's shared viewer. */
export function ChatViewerBridges({
  viewerPanelRef,
}: {
  viewerPanelRef: React.MutableRefObject<SessionViewerPanelHandle | null>;
}) {
  const quiz = useQuizFollowupController();
  const geogebra = useGeogebraTabOpener();

  useEffect(() => {
    quiz.setOpenTabHandler((context) => {
      viewerPanelRef.current?.openQuizFollowupTab(context);
    });
    return () => quiz.setOpenTabHandler(null);
  }, [quiz, viewerPanelRef]);

  useEffect(() => {
    if (!geogebra) return;
    geogebra.setOpenHandler((payload) => {
      viewerPanelRef.current?.openGeogebraTab(payload);
    });
    return () => geogebra.setOpenHandler(null);
  }, [geogebra, viewerPanelRef]);

  useEffect(() => {
    const onOpen = (e: Event) => viewerPanelRef.current?.openHtmlTab(String((e as CustomEvent).detail?.html ?? ""));
    window.addEventListener("pi-tutor:open-html", onOpen);
    return () => window.removeEventListener("pi-tutor:open-html", onOpen);
  }, [viewerPanelRef]);

  return null;
}
