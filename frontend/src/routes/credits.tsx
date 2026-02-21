import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";

const LICENSE_URL = "https://github.com/OpenHands/OpenHands/blob/main/LICENSE";
const CREDITS_URL =
  "https://github.com/OpenHands/OpenHands/blob/main/CREDITS.md";

function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-primary underline underline-offset-2"
    >
      {children}
    </a>
  );
}

function CreditsScreen() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const isSaas = config?.app_mode === "saas";

  const releaseInfo = useMemo(
    () =>
      [
        ...(isSaas
          ? [
              {
                label: t(I18nKey.CREDITS$OPENHANDS_ENTERPRISE_EDITION),
                version: __OPENHANDS_ENTERPRISE_VERSION__,
              },
            ]
          : []),
        {
          label: t(I18nKey.CREDITS$OPENHANDS_WEB),
          version: __OPENHANDS_WEB_VERSION__,
        },
        {
          label: t(I18nKey.CREDITS$SOFTWARE_AGENT_SDK),
          version: __OPENHANDS_SDK_VERSION__,
        },
      ].filter((item) => item.version.trim().length > 0),
    [isSaas, t],
  );

  return (
    <main
      data-testid="credits-screen"
      className="px-6 pt-[35px] pb-10 h-full overflow-y-auto rounded-xl lg:px-[42px] lg:pt-[42px] custom-scrollbar-always"
    >
      <div className="max-w-[900px] flex flex-col gap-8">
        <div className="flex flex-col gap-2">
          <Typography.H2>{t(I18nKey.CREDITS$TITLE)}</Typography.H2>
          <Typography.Paragraph className="text-gray-300">
            {t(I18nKey.CREDITS$ACKNOWLEDGEMENT)}
          </Typography.Paragraph>
        </div>

        <section className="flex flex-col gap-3">
          <Typography.H3 className="text-gray-200">
            {t(I18nKey.CREDITS$RELEASE_INFORMATION)}
          </Typography.H3>
          <div className="rounded-xl border border-tertiary bg-base-secondary p-4">
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {releaseInfo.map((item) => (
                <div key={item.label} className="flex flex-col gap-1">
                  <dt className="text-xs font-semibold text-gray-400">
                    {item.label}
                  </dt>
                  <dd className="text-sm text-white font-mono">
                    {item.version}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {isSaas && (
          <section className="flex flex-col gap-3">
            <Typography.H3 className="text-gray-200">
              {t(I18nKey.CREDITS$COPYRIGHT)}
            </Typography.H3>
            <Typography.Paragraph className="text-gray-300">
              {t(I18nKey.CREDITS$COPYRIGHT_ALLHANDS_2026)}
            </Typography.Paragraph>
          </section>
        )}

        <section className="flex flex-col gap-3">
          <Typography.H3 className="text-gray-200">
            {t(I18nKey.CREDITS$TITLE)}
          </Typography.H3>
          <ul className="list-disc pl-5 flex flex-col gap-2 text-sm text-gray-300">
            <li>
              {t(I18nKey.CREDITS$MIT_LICENSE_PREFIX)}{" "}
              <ExternalLink href={LICENSE_URL}>
                {t(I18nKey.CREDITS$LICENSE)}
              </ExternalLink>
            </li>
            <li>
              {t(I18nKey.CREDITS$THIRD_PARTY_PREFIX)}{" "}
              <ExternalLink href={CREDITS_URL}>
                {t(I18nKey.CREDITS$CREDITS_MD)}
              </ExternalLink>
            </li>
          </ul>
        </section>
      </div>
    </main>
  );
}

export default CreditsScreen;
