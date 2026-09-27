/** Visual icons for each component type, extracted from Claude Design SVGs. */

const ICONS: Record<string, { viewBox: string; el: React.ReactNode }> = {
  resistor: {
    viewBox: '0 0 30 14',
    el: (
      <>
        <rect x="2" y="6" width="8" height="0.6" fill="#d97706" />
        <rect x="20" y="6" width="8" height="0.6" fill="#d97706" />
        <rect x="9" y="4" width="12" height="4.2" rx="0.8" fill="#e8d4a0" stroke="#a3862f" strokeWidth="0.25" />
        <rect x="12.5" y="4" width="0.9" height="4.2" fill="#7a4a1e" />
        <rect x="15" y="4" width="0.9" height="4.2" fill="#b91c1c" />
        <circle cx="2" cy="6.3" r="0.9" fill="#d97706" />
        <circle cx="28" cy="6.3" r="0.9" fill="#d97706" />
      </>
    ),
  },
  led: {
    viewBox: '0 0 24 20',
    el: (
      <>
        <line x1="9" y1="18" x2="9" y2="10" stroke="#6b7280" strokeWidth="0.6" />
        <line x1="15" y1="18" x2="15" y2="10" stroke="#6b7280" strokeWidth="0.6" />
        <ellipse cx="12" cy="7" rx="5.5" ry="6.2" fill="#ef4444" stroke="#7f1d1d" strokeWidth="0.4" />
        <circle cx="9" cy="18" r="0.9" fill="#6b7280" />
        <circle cx="15" cy="18" r="0.9" fill="#6b7280" />
        <text x="9" y="19.4" fontSize="1.8" textAnchor="middle" fill="#6b7280">+</text>
        <text x="15" y="19.4" fontSize="1.8" textAnchor="middle" fill="#6b7280">−</text>
      </>
    ),
  },
  esp32_devkit_v1_30: {
    viewBox: '0 0 60 26',
    el: (
      <>
        <rect x="4" y="4" width="52" height="18" rx="1.5" fill="rgba(28,45,90,0.12)" stroke="#2f5fb3" strokeWidth="0.4" />
        <rect x="50" y="4" width="7" height="4" fill="#94a3b8" stroke="#5b6754" strokeWidth="0.2" />
        <text x="30" y="14" fontSize="3" fontWeight="600" textAnchor="middle" fill="#1f2937">ESP32 DevKit</text>
        <text x="30" y="19" fontSize="1.6" textAnchor="middle" fill="#5b6754">30-pin</text>
        {[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14].map((i) => (
          <circle key={`t${i}`} cx={6 + i * 3.4} cy="2" r="0.9" fill="#3b82f6" />
        ))}
        {[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14].map((i) => (
          <circle key={`b${i}`} cx={6 + i * 3.4} cy="24" r="0.9" fill="#3b82f6" />
        ))}
      </>
    ),
  },
  potentiometer: {
    viewBox: '0 0 26 22',
    el: (
      <>
        <line x1="6" y1="18" x2="6" y2="12" stroke="#374151" strokeWidth="0.6" />
        <line x1="13" y1="18" x2="13" y2="12" stroke="#374151" strokeWidth="0.6" />
        <line x1="20" y1="18" x2="20" y2="12" stroke="#374151" strokeWidth="0.6" />
        <rect x="2" y="4" width="22" height="9" rx="0.8" fill="#6b7280" stroke="#374151" strokeWidth="0.3" />
        <circle cx="13" cy="8.5" r="4.4" fill="#374151" />
        <circle cx="6" cy="18" r="0.9" fill="#b45309" />
        <circle cx="13" cy="18" r="0.9" fill="#b45309" />
        <circle cx="20" cy="18" r="0.9" fill="#b45309" />
      </>
    ),
  },
  capacitor_electrolytic: {
    viewBox: '0 0 20 23',
    el: (
      <>
        <line x1="7" y1="18" x2="7" y2="14" stroke="#2563eb" strokeWidth="0.6" />
        <line x1="13" y1="18" x2="13" y2="14" stroke="#2563eb" strokeWidth="0.6" />
        <rect x="3" y="2" width="14" height="12" rx="2.5" fill="#2b5fa8" stroke="#173a6b" strokeWidth="0.3" />
        <rect x="9.6" y="2" width="1" height="12" fill="#e5e7eb" />
        <circle cx="7" cy="18" r="0.9" fill="#2563eb" />
        <circle cx="13" cy="18" r="0.9" fill="#2563eb" />
        <text x="7" y="19.6" fontSize="1.6" textAnchor="middle" fill="#6b7280">+</text>
        <text x="13" y="19.6" fontSize="1.6" textAnchor="middle" fill="#6b7280">−</text>
      </>
    ),
  },
  capacitor_ceramic: {
    viewBox: '0 0 20 14',
    el: (
      <>
        <line x1="7" y1="7" x2="9" y2="7" stroke="#0891b2" strokeWidth="0.6" />
        <line x1="13" y1="7" x2="15" y2="7" stroke="#0891b2" strokeWidth="0.6" />
        <ellipse cx="11" cy="7" rx="5" ry="4.2" fill="#cbd5c9" stroke="#5b6754" strokeWidth="0.3" />
        <circle cx="7" cy="7" r="0.9" fill="#0891b2" />
        <circle cx="15" cy="7" r="0.9" fill="#0891b2" />
      </>
    ),
  },
  transistor_npn: {
    viewBox: '0 0 24 23.5',
    el: (
      <>
        <line x1="6" y1="18" x2="6" y2="11" stroke="#1f2328" strokeWidth="0.6" />
        <line x1="12" y1="18" x2="12" y2="11" stroke="#1f2328" strokeWidth="0.6" />
        <line x1="18" y1="18" x2="18" y2="11" stroke="#1f2328" strokeWidth="0.6" />
        <rect x="2" y="4" width="20" height="8" rx="2.5" fill="#1f2328" stroke="#000" strokeWidth="0.25" />
        <circle cx="6" cy="18" r="0.9" fill="#374151" />
        <circle cx="12" cy="18" r="0.9" fill="#374151" />
        <circle cx="18" cy="18" r="0.9" fill="#374151" />
        <text x="6" y="20" fontSize="1.5" textAnchor="middle" fill="#9ca3af">E</text>
        <text x="12" y="20" fontSize="1.5" textAnchor="middle" fill="#9ca3af">B</text>
        <text x="18" y="20" fontSize="1.5" textAnchor="middle" fill="#9ca3af">C</text>
      </>
    ),
  },
  diode: {
    viewBox: '0 0 28 14',
    el: (
      <>
        <line x1="4" y1="7" x2="9" y2="7" stroke="#64748b" strokeWidth="0.6" />
        <line x1="19" y1="7" x2="24" y2="7" stroke="#64748b" strokeWidth="0.6" />
        <rect x="9" y="4.5" width="10" height="5" rx="0.6" fill="#26282c" />
        <rect x="16.5" y="4.5" width="0.9" height="5" fill="#e5e7eb" />
        <circle cx="4" cy="7" r="0.9" fill="#64748b" />
        <circle cx="24" cy="7" r="0.9" fill="#64748b" />
      </>
    ),
  },
  i2c_module: {
    viewBox: '0 0 30 22',
    el: (
      <>
        {[7, 12, 17, 22].map((x) => (
          <line key={x} x1={x} y1="4" x2={x} y2="8" stroke="#0f4d2f" strokeWidth="0.6" />
        ))}
        <rect x="4" y="8" width="21" height="9" rx="0.8" fill="#1f7a4d" stroke="#0f4d2f" strokeWidth="0.3" />
        <rect x="10.5" y="11" width="7" height="4.5" fill="#1f2328" />
        {[7, 12, 17, 22].map((x) => (
          <circle key={x} cx={x} cy="4" r="0.9" fill="#16a34a" />
        ))}
        <text x="7" y="2.4" fontSize="1.5" textAnchor="middle" fill="#0f4d2f">VCC</text>
        <text x="12" y="2.4" fontSize="1.5" textAnchor="middle" fill="#0f4d2f">GND</text>
        <text x="17" y="2.4" fontSize="1.5" textAnchor="middle" fill="#0f4d2f">SCL</text>
        <text x="22" y="2.4" fontSize="1.5" textAnchor="middle" fill="#0f4d2f">SDA</text>
      </>
    ),
  },
}

const TYPE_LABEL: Record<string, string> = {
  resistor: 'Resistor',
  led: 'LED',
  esp32_devkit_v1_30: 'ESP32',
  potentiometer: 'Potentiometer',
  capacitor_electrolytic: 'Electrolytic Cap',
  capacitor_ceramic: 'Ceramic Cap',
  transistor_npn: 'NPN Transistor',
  diode: 'Diode',
  i2c_module: 'I²C Module',
}

export function componentLabel(type: string): string {
  return TYPE_LABEL[type] ?? type
}

export function ComponentIcon({ type, size = 36 }: { type: string; size?: number }) {
  const icon = ICONS[type]
  if (!icon) return null
  const [, , vw, vh] = icon.viewBox.split(' ').map(Number)
  const width = Math.round(size * (vw / vh))
  return (
    <svg
      viewBox={icon.viewBox}
      width={width}
      height={size}
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ display: 'block', flexShrink: 0 }}
    >
      {icon.el}
    </svg>
  )
}
