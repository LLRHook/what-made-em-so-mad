import { forwardRef } from "react";

type PlayerProps = {
  src: string;
  onTimeUpdate?: (seconds: number) => void;
};

export const Player = forwardRef<HTMLVideoElement, PlayerProps>(function Player({ src, onTimeUpdate }, ref) {
  return (
    <video
      ref={ref}
      src={src}
      controls
      preload="metadata"
      className="player"
      data-testid="player"
      onTimeUpdate={(e) => onTimeUpdate?.(e.currentTarget.currentTime)}
    />
  );
});
