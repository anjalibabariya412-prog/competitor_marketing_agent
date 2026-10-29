import React from 'react';

/**
 * Standard fixed icon dimensions:
 * - 'sm': 16px (inline text, pills, badges)
 * - 'md': 20px (buttons, input actions)
 * - 'lg': 24px (card headers)
 */
function getSizeStyle(size) {
  if (size === 'lg') return { width: 24, height: 24, minWidth: 24, minHeight: 24 };
  if (size === 'md') return { width: 20, height: 20, minWidth: 20, minHeight: 20 };
  return { width: 16, height: 16, minWidth: 16, minHeight: 16 };
}

export function CheckIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} viewBox="0 0 20 20" fill="currentColor" {...props}>
      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
    </svg>
  );
}

export function AlertCircleIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} viewBox="0 0 20 20" fill="currentColor" {...props}>
      <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
    </svg>
  );
}

export function ShieldAlertIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  );
}

export function ArrowLeftIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
    </svg>
  );
}

export function ArrowRightIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3" />
    </svg>
  );
}

export function ExternalLinkIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
    </svg>
  );
}

export function PlusIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
    </svg>
  );
}

export function TrashIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
    </svg>
  );
}

export function RefreshIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
    </svg>
  );
}

export function SearchIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" strokeLinecap="round" />
    </svg>
  );
}

export function GlobeIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <circle cx="12" cy="12" r="10" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
    </svg>
  );
}

export function ChevronDownIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  );
}

export function ChevronUpIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
    </svg>
  );
}

export function SunIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <circle cx="12" cy="12" r="5" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
    </svg>
  );
}

export function MoonIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
    </svg>
  );
}

export function PackageIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
    </svg>
  );
}

export function BriefcaseIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M16 21V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v16" />
    </svg>
  );
}

export function ShoppingBagIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z" />
    </svg>
  );
}

export function UsersIcon({ size = 'sm', className = '', ...props }) {
  return (
    <svg style={getSizeStyle(size)} className={`ui-icon ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
    </svg>
  );
}


