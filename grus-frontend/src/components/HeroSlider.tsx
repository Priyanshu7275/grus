"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronLeft, ChevronRight, LucideIcon } from "lucide-react";

export interface Slide {
  icon: LucideIcon;
  eyebrow: string;
  title: string;
  body: string;
  gradient: string; // tailwind gradient classes
  video?: string; // e.g. "/videos/slide1.mp4"
  image?: string; // e.g. "/images/slide3.jpg"
}

export function HeroSlider({ slides, intervalMs = 6000 }: { slides: Slide[]; intervalMs?: number }) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => setIndex((i) => (i + 1) % slides.length), intervalMs);
    return () => clearInterval(t);
  }, [paused, slides.length, intervalMs]);

  const slide = slides[index];
  const Icon = slide.icon;

  return (
    <div
      className="group relative w-full overflow-hidden rounded-3xl shadow-lift"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div
  className={`relative h-[460px] sm:h-[540px] w-full overflow-hidden ${
    slide.video || slide.image ? "bg-ink-900" : `bg-gradient-to-br ${slide.gradient}`
  }`}
>
  {slide.video && (
    <video
      key={slide.video}
      autoPlay
      loop
      muted
      playsInline
      className="absolute inset-0 h-full w-full object-cover"
    >
      <source src={slide.video} type="video/mp4" />
    </video>
  )}
  {slide.image && !slide.video && (
    <img src={slide.image} alt="" className="absolute inset-0 h-full w-full object-cover" />
  )}
  {(slide.video || slide.image) && (
    <div className={`absolute inset-0 bg-gradient-to-br ${slide.gradient} opacity-60`} />
  )}
        {/* decorative medical-icon texture, echoes the mascot's background */}
        <div
          className="absolute inset-0 opacity-[0.12]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 20% 20%, white 2px, transparent 2px), radial-gradient(circle at 70% 60%, white 2px, transparent 2px), radial-gradient(circle at 40% 80%, white 2px, transparent 2px)",
            backgroundSize: "60px 60px, 90px 90px, 70px 70px",
          }}
        />

        <AnimatePresence mode="wait">
          <motion.div
            key={index}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -14 }}
            transition={{ duration: 0.45, ease: "easeOut" }}
            className="relative z-10 flex h-full flex-col items-start justify-center px-8 sm:px-14 max-w-2xl"
          >
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-white/20 backdrop-blur">
              <Icon className="h-6 w-6 text-white" />
            </div>
            <p className="mb-2 font-mono text-xs font-semibold uppercase tracking-widest text-white/80">
              {slide.eyebrow}
            </p>
            <h2 className="text-2xl sm:text-3xl font-extrabold leading-tight text-white">{slide.title}</h2>
            <p className="mt-3 max-w-xl text-sm sm:text-base leading-relaxed text-white/90">{slide.body}</p>
          </motion.div>
        </AnimatePresence>

        {/* arrows */}
        <button
          aria-label="Previous slide"
          onClick={() => setIndex((i) => (i - 1 + slides.length) % slides.length)}
          className="absolute left-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/20 p-2 text-white opacity-0 backdrop-blur transition hover:bg-white/30 group-hover:opacity-100"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <button
          aria-label="Next slide"
          onClick={() => setIndex((i) => (i + 1) % slides.length)}
          className="absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/20 p-2 text-white opacity-0 backdrop-blur transition hover:bg-white/30 group-hover:opacity-100"
        >
          <ChevronRight className="h-5 w-5" />
        </button>

        {/* dots */}
        <div className="absolute bottom-5 left-1/2 z-10 flex -translate-x-1/2 gap-2">
          {slides.map((_, i) => (
            <button
              key={i}
              aria-label={`Go to slide ${i + 1}`}
              onClick={() => setIndex(i)}
              className={`h-1.5 rounded-full transition-all ${
                i === index ? "w-6 bg-white" : "w-1.5 bg-white/50 hover:bg-white/70"
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
