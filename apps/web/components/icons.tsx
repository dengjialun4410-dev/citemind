import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

export const SparkIcon = (props: IconProps) => <IconBase {...props}><path d="m12 3-1.2 4.1a5.3 5.3 0 0 1-3.7 3.7L3 12l4.1 1.2a5.3 5.3 0 0 1 3.7 3.7L12 21l1.2-4.1a5.3 5.3 0 0 1 3.7-3.7L21 12l-4.1-1.2a5.3 5.3 0 0 1-3.7-3.7L12 3Z" /></IconBase>;
export const LibraryIcon = (props: IconProps) => <IconBase {...props}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" /></IconBase>;
export const FileIcon = (props: IconProps) => <IconBase {...props}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /><path d="M8 13h8M8 17h5" /></IconBase>;
export const SearchIcon = (props: IconProps) => <IconBase {...props}><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></IconBase>;
export const UploadIcon = (props: IconProps) => <IconBase {...props}><path d="M12 16V4m0 0L7 9m5-5 5 5" /><path d="M20 15v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-4" /></IconBase>;
export const ArrowIcon = (props: IconProps) => <IconBase {...props}><path d="m5 12 14 0m-6-6 6 6-6 6" /></IconBase>;
export const QuoteIcon = (props: IconProps) => <IconBase {...props}><path d="M3 21c3 0 7-1 7-8V5c0-1.2-.8-2-2-2H5c-1.2 0-2 .8-2 2v6c0 1.2.8 2 2 2h3c0 4-1 5-5 6" /><path d="M14 21c3 0 7-1 7-8V5c0-1.2-.8-2-2-2h-3c-1.2 0-2 .8-2 2v6c0 1.2.8 2 2 2h3c0 4-1 5-5 6" /></IconBase>;
export const CheckIcon = (props: IconProps) => <IconBase {...props}><path d="m5 12 4 4L19 6" /></IconBase>;
export const PlusIcon = (props: IconProps) => <IconBase {...props}><path d="M12 5v14M5 12h14" /></IconBase>;
