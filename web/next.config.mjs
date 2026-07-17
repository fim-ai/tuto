/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingIncludes: {
    '/report': ['../docs/*.md'],
    '/report/zh': ['../docs/*.md'],
  },
};
export default nextConfig;
