import { useState, useEffect } from 'react';

export type Orientation = 'landscape' | 'portrait';

export function useDeviceOrientation(): Orientation {
  const [orientation, setOrientation] = useState<Orientation>(() => {
    if (typeof window === 'undefined') return 'landscape';
    return window.innerWidth > window.innerHeight ? 'landscape' : 'portrait';
  });

  useEffect(() => {
    const handleResize = () => {
      const newOrientation = window.innerWidth > window.innerHeight ? 'landscape' : 'portrait';
      setOrientation(newOrientation);
    };

    window.addEventListener('resize', handleResize);
    
    const mediaQuery = window.matchMedia('(orientation: portrait)');
    const handleOrientationChange = (e: MediaQueryListEvent) => {
      setOrientation(e.matches ? 'portrait' : 'landscape');
    };
    
    mediaQuery.addEventListener('change', handleOrientationChange);

    return () => {
      window.removeEventListener('resize', handleResize);
      mediaQuery.removeEventListener('change', handleOrientationChange);
    };
  }, []);

  return orientation;
}
