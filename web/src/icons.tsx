import React from 'react';
import { ContentStatus, ExecutionStatus, ResourceCategory } from './types';

interface IconProps {
  size?: number;
  className?: string;
}

export const StatusIcon: React.FC<{ status: ExecutionStatus; size?: number }> = ({
  status,
  size = 14,
}) => {
  switch (status) {
    case 'completed':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1.5" y="1.5" width="13" height="13" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4.5 8.2L7 10.7L11.8 5.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" />
        </svg>
      );
    case 'running':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 2" />
          <circle cx="8" cy="8" r="2.5" fill="currentColor" />
        </svg>
      );
    case 'failed':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1.5" y="1.5" width="13" height="13" stroke="currentColor" strokeWidth="1.5" />
          <path d="M5 5L11 11M11 5L5 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" />
        </svg>
      );
    case 'stopped':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <polygon points="5,1.5 11,1.5 14.5,5 14.5,11 11,14.5 5,14.5 1.5,11 1.5,5" stroke="currentColor" strokeWidth="1.5" />
          <rect x="5.5" y="5.5" width="5" height="5" fill="currentColor" />
        </svg>
      );
    case 'skipped':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1.5" y="1.5" width="13" height="13" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2.5 2" />
          <path d="M4 12L12 4" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      );
    case 'pending':
    default:
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="2" y="2" width="12" height="12" stroke="currentColor" strokeWidth="1.4" />
          <circle cx="8" cy="8" r="1.5" fill="currentColor" />
        </svg>
      );
  }
};

export const ContentStateIcon: React.FC<{ status: ContentStatus; size?: number }> = ({
  status,
  size = 13,
}) => {
  if (status === 'cooled') {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <polygon points="8,1.5 14,5 14,11 8,14.5 2,11 2,5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="8" cy="8" r="2.2" fill="currentColor" />
      </svg>
    );
  }
  if (status === 'ready-for-cool') {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <polygon points="8,1.5 14.5,8 8,14.5 1.5,8" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5.5 8L7.3 9.8L10.8 6.2" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="2.5" y="2.5" width="11" height="11" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5 8H11" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
};

export const CategoryIcon: React.FC<{ category: ResourceCategory; size?: number }> = ({
  category,
  size = 14,
}) => {
  switch (category) {
    case 'kernel':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M5.5 4.5L2 8L5.5 11.5M10.5 4.5L14 8L10.5 11.5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M9 3L7 13" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      );
    case 'profile':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2 13.5H14M4 11V7M8 11V3.5M12 11V5.5" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
    case 'experiment':
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="2" y="2.5" width="12" height="11" stroke="currentColor" strokeWidth="1.4" />
          <path d="M2 6.5H14M6.5 6.5V13.5" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      );
    case 'idea':
    case 'summary':
    default:
      return (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="2.5" y="1.5" width="11" height="13" stroke="currentColor" strokeWidth="1.4" />
          <path d="M5 5.5H11M5 8.5H11M5 11.5H8.5" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      );
  }
};

export const TreeIcon: React.FC<IconProps> = ({ size = 15 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <rect x="6" y="1.5" width="4" height="3.5" stroke="currentColor" strokeWidth="1.4" />
    <rect x="1.5" y="11" width="4" height="3.5" stroke="currentColor" strokeWidth="1.4" />
    <rect x="10.5" y="11" width="4" height="3.5" stroke="currentColor" strokeWidth="1.4" />
    <path d="M8 5V8M3.5 11V8H12.5V11" stroke="currentColor" strokeWidth="1.4" />
  </svg>
);

export const TimelineIcon: React.FC<IconProps> = ({ size = 15 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M2 4H14M2 8H14M2 12H14" stroke="currentColor" strokeWidth="1.3" strokeDasharray="2 2" />
    <rect x="3" y="2.5" width="5" height="3" fill="currentColor" />
    <rect x="6" y="6.5" width="6" height="3" fill="currentColor" />
    <rect x="4" y="10.5" width="7" height="3" fill="currentColor" />
  </svg>
);

export const LinkShareIcon: React.FC<IconProps> = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M6.5 9.5L9.5 6.5M5.5 7L4 8.5C2.9 9.6 2.9 11.4 4 12.5C5.1 13.6 6.9 13.6 8 12.5L9.5 11M10.5 9L12 7.5C13.1 6.4 13.1 4.6 12 3.5C10.9 2.4 9.1 2.4 8 3.5L6.5 5" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);

export const ChevronIcon: React.FC<{ direction?: 'down' | 'right' | 'up'; size?: number }> = ({
  direction = 'down',
  size = 13,
}) => {
  const rotate = direction === 'right' ? -90 : direction === 'up' ? 180 : 0;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      style={{ transform: `rotate(${rotate}deg)` }}
      aria-hidden="true"
    >
      <path d="M4 6L8 10L12 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" />
    </svg>
  );
};
