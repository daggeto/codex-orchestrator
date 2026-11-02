import { createRoot } from 'react-dom/client';
import { AppearanceProvider, initializeAppearance } from './appearance';
import { App } from './App';
import './styles.css';

initializeAppearance();
createRoot(document.getElementById('root')!).render(<AppearanceProvider><App /></AppearanceProvider>);
