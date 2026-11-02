import { useEffect, useState } from 'react';
export function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 999px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 999px)');
    const update = () => setMobile(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return mobile;
}
