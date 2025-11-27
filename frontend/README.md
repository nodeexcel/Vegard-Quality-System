# KvalitetTakst Frontend

Next.js frontend application for uploading and viewing building condition report analyses.

## Quick Start

1. Install dependencies:
```bash
npm install
```

2. Configure environment:
```bash
cp .env.local.example .env.local
# Edit .env.local with API URL
```

3. Start development server:
```bash
npm run dev
```

Visit http://localhost:3000

## Build for Production

```bash
npm run build
npm start
```

## Project Structure

- `app/page.tsx`: Main upload page
- `app/results/[id]/page.tsx`: Results display page
- `app/layout.tsx`: Root layout
- `app/globals.css`: Global styles with Tailwind CSS

## Technologies

- Next.js 14 (App Router)
- React 18
- TypeScript
- Tailwind CSS
- Axios for API calls

