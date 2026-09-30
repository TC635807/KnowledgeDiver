import { useEffect, useRef } from 'react';

// Debounced auto-save hook. When the value changes, calls saveFunction after delay.
export function useAutoSave<T>(value: T, saveFunction: (v: T) => void, delay: number = 500) {
  const timerRef = useRef<number | undefined>(undefined);
  // Serialize value for a stable dependency in case of object identity changes
  const dep = typeof value === 'string' ? value : JSON.stringify(value);

  useEffect(() => {
    // Clear any existing timer
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      saveFunction(value);
    }, delay);
    // Cleanup on unmount or when value changes
    return () => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep, delay]);
}
