"use client";

import { motion, AnimatePresence } from "framer-motion";
import { useState } from "react";

/**
 * GRUS's mascot — a crane in a doctor's coat (GRUS is the Latin genus
 * name for cranes). The video has its own light-blue background baked
 * in, so it's always shown inside a bounded rounded card rather than
 * floating free over arbitrary page backgrounds.
 */
export function Mascot({
  tip,
  className,
  rounded = "rounded-3xl",
}: {
  tip?: string;
  className?: string;
  rounded?: string;
}) {
  const [show, setShow] = useState(true);

  return (
    <div className={className}>
      <div className={`relative overflow-hidden ${rounded} shadow-lift`}>
        <video
          src="/grus-mascot.mp4"
          autoPlay
          loop
          muted
          playsInline
          className="block h-full w-full object-cover"
        />
        <AnimatePresence>
          {tip && show && (
            <motion.button
              type="button"
              onClick={() => setShow(false)}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ delay: 0.6 }}
              className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full glass px-4 py-1.5 text-sm font-medium text-ink-700 shadow-soft"
              aria-label={tip}
            >
              {tip}
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
