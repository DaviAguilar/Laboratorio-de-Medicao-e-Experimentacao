import os
import requests
import json
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import csv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

ENDPOINT = 'https://api.github.com/graphql'

# Minimal GraphQL query template for fetching repo names only (paginated)
MINIMAL_QUERY_TEMPLATE = '''
query($after: String) {
  search(query: "stars:>1 sort:stars-desc", type: REPOSITORY, first: 25, after: $after) {
    pageInfo {
      endCursor
      hasNextPage
    }
    edges {
      node {
        ... on Repository {
          nameWithOwner
        }
      }
    }
  }
}
'''

# GraphQL query for a single repository's full data
REPO_QUERY_TEMPLATE = '''
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    createdAt
    pullRequests(states: MERGED) {
      totalCount
    }
    releases {
      totalCount
    }
    updatedAt
    primaryLanguage {
      name
    }
    issues {
      totalCount
    }
    closedIssues: issues(states: CLOSED) {
      totalCount
    }
  }
}
'''

# For debugging: Simple query remains
SIMPLE_QUERY = '''
query {
  repository(owner: "torvalds", name: "linux") {
    nameWithOwner
    stargazerCount
  }
}
'''


def run_query(query, variables=None, retries=3):
    headers = {
        'Authorization': f'Bearer {GITHUB_TOKEN}',
        'Content-Type': 'application/json',
        'User-Agent': 'grok-github-query-tool/1.0'
    }
    for attempt in range(retries):
        try:
            response = requests.post(ENDPOINT, headers=headers, json={'query': query, 'variables': variables})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            if response.status_code == 502 and attempt < retries - 1:
                print(f"502 error on attempt {attempt + 1}/{retries}. Retrying in 10 seconds...")
                time.sleep(10)
            else:
                print(f"HTTP error: {http_err}")
                print(f"Response: {response.text}")
        except Exception as e:
            print(f"Other error: {e}")
    return None


def main():
    # Test simple query
    print("Testing simple query...")
    simple_result = run_query(SIMPLE_QUERY)
    if simple_result:
        print("Simple query succeeded:", json.dumps(simple_result, indent=2))
    else:
        print("Simple query failed. Check token and network.")
        return

    csv_filename = f"github_repos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    report_filename = "../report_initial.md"

    # Define headers for CSV
    headers = [
        "Repository",
        "Age (days)",
        "Created Date",
        "Accepted PRs",
        "Releases",
        "Days Since Update",
        "Primary Language",
        "Closed Issues Ratio"
    ]

    # Collect all repo data
    repo_data_list = []

    # Step 1: Fetch list of top 1000 repo names using minimal paginated search
    print("\nFetching list of top 1000 repo names...")
    all_repo_names = []
    after = None
    has_next = True
    pages_fetched = 0
    max_pages = 40  # For 100 repos (25 * 4)

    while has_next and pages_fetched < max_pages:
        variables = {'after': after}
        result = run_query(MINIMAL_QUERY_TEMPLATE, variables)
        if not result:
            print("Failed to fetch repo list. Aborting.")
            return
        if 'errors' in result:
            print("Errors in list fetch:", result['errors'])
            return

        search = result['data']['search']
        edges = search['edges']
        if not edges:
            print(f"Warning: No repos returned for page {pages_fetched + 1}. Full response:",
                  json.dumps(result, indent=2))
        else:
            for edge in edges:
                all_repo_names.append(edge['node']['nameWithOwner'])
            print(f"Fetched page {pages_fetched + 1}: {len(edges)} repos")

        page_info = search['pageInfo']
        after = page_info['endCursor']
        has_next = page_info['hasNextPage']
        pages_fetched += 1

    print(f"Collected {len(all_repo_names)} repo names.")

    # Step 2: Fetch full data for each repo one by one and collect data
    print(f"\nFetching data for each repo...")
    for idx, name_with_owner in enumerate(all_repo_names, 1):
        print(f"\nProcessing repo {idx}/{len(all_repo_names)}: {name_with_owner}")
        owner, name = name_with_owner.split('/')
        variables = {'owner': owner, 'name': name}
        result = run_query(REPO_QUERY_TEMPLATE, variables)
        if not result:
            print("Failed to fetch data for this repo.")
            continue
        if 'errors' in result:
            print("Errors:", result['errors'])
            continue

        repo = result['data']['repository']
        if not repo:
            print("No data returned for this repo.")
            continue

        created_at = datetime.fromisoformat(repo['createdAt'].replace('Z', '+00:00'))
        age_days = (datetime.now(timezone.utc) - created_at).days
        prs_accepted = repo['pullRequests']['totalCount']
        releases_count = repo['releases']['totalCount']
        updated_at = datetime.fromisoformat(repo['updatedAt'].replace('Z', '+00:00'))
        days_since_update = (datetime.now(timezone.utc) - updated_at).days
        language = repo['primaryLanguage']['name'] if repo['primaryLanguage'] else 'Unknown'
        total_issues = repo['issues']['totalCount']
        closed_issues = repo['closedIssues']['totalCount']
        issues_ratio = (closed_issues / total_issues) if total_issues > 0 else 0

        # Prepare data row
        row = [
            name_with_owner,
            age_days,
            created_at.date().strftime('%Y-%m-%d'),
            prs_accepted,
            releases_count,
            days_since_update,
            language,
            round(issues_ratio, 2)
        ]

        repo_data_list.append(row)

        print(f"Repository: {name_with_owner}")
        print(f"  Age (days): {age_days} (Created on {created_at.date()})")
        print(f"  Accepted PRs: {prs_accepted}")
        print(f"  Releases: {releases_count}")
        print(f"  Days since last update: {days_since_update}")
        print(f"  Primary Language: {language}")
        print(f"  Closed Issues Ratio: {issues_ratio:.2f}")
        print("---")

        time.sleep(1)  # Delay to avoid rate limiting

    # Write to CSV
    print(f"\nSaving data to CSV file: {csv_filename}")
    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(repo_data_list)

    # Create initial report with informal hypotheses
    print(f"\nGenerating initial report: {report_filename}")
    with open(report_filename, 'w', encoding='utf-8') as f:
        f.write("# Relatório Inicial - Laboratório 01\n\n")
        f.write("## Introdução e Hipóteses Informais\n\n")
        f.write(
            "Neste relatório inicial, definimos hipóteses informais para cada questão de pesquisa (RQ) com base em expectativas comuns sobre repositórios populares no GitHub. Essas hipóteses serão testadas com os dados coletados.\n\n")

        f.write("### RQ 01: Sistemas populares são maduros/antigos?\n")
        f.write(
            "Hipótese: Sim, a maioria dos repositórios populares tem mais de 5 anos de idade (mediana > 1825 dias), pois projetos maduros atraem mais estrelas ao longo do tempo.\n\n")

        f.write("### RQ 02: Sistemas populares recebem muita contribuição externa?\n")
        f.write(
            "Hipótese: Sim, repositórios populares recebem muitas contribuições, com mediana de pull requests aceitas > 1000, devido à visibilidade.\n\n")

        f.write("### RQ 03: Sistemas populares lançam releases com frequência?\n")
        f.write(
            "Hipótese: Não necessariamente, muitos projetos populares são bibliotecas ou frameworks com poucas releases formais (mediana < 50), preferindo atualizações contínuas.\n\n")

        f.write("### RQ 04: Sistemas populares são atualizados com frequência?\n")
        f.write(
            "Hipótese: Sim, a maioria é atualizada recentemente, com mediana de dias desde a última atualização < 30 dias.\n\n")

        f.write("### RQ 05: Sistemas populares são escritos nas linguagens mais populares?\n")
        f.write(
            "Hipótese: Sim, a maioria usa linguagens como JavaScript, Python e Java, com contagem mostrando essas como as top 3.\n\n")

        f.write("### RQ 06: Sistemas populares possuem um alto percentual de issues fechadas?\n")
        f.write(
            "Hipótese: Sim, repositórios populares mantêm issues bem gerenciadas, com mediana de razão de issues fechadas > 0.8 (80%).\n\n")

        f.write("## Próximos Passos\n")
        f.write(
            "Na próxima sprint (Lab01S03), analisaremos os dados do arquivo CSV para calcular medianas, contagens e visualizar os resultados, comparando com essas hipóteses.\n")

    print(f"All {len(repo_data_list)} repos processed. CSV and initial report generated.")


if __name__ == "__main__":
    main()