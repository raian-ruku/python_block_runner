# %% Section 1: Setup and Imports
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

print("NumPy version:", np.__version__)
print("Matplotlib backend:", plt.get_backend())
np.random.seed(42)

# %% Section 2: Line Chart — Sine and Cosine
x = np.linspace(0, 4 * np.pi, 300)

fig, ax = plt.subplots(figsize=(9, 4))
ax.plot(x, np.sin(x), label="sin(x)", color="#4A90D9", linewidth=2)
ax.plot(x, np.cos(x), label="cos(x)", color="#E57373", linewidth=2, linestyle="--")
ax.set_title("Sine and Cosine Waves", fontsize=14)
ax.set_xlabel("x (radians)")
ax.set_ylabel("Amplitude")
ax.legend()
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()
print("Line chart rendered.")

# %% Section 3: Histogram — Normal Distribution
data = np.random.normal(loc=0, scale=1, size=2000)

fig, ax = plt.subplots(figsize=(8, 4))
ax.hist(data, bins=50, color="#6FCF97", edgecolor="white", alpha=0.85)
ax.set_title("Normal Distribution Histogram (n=2000)", fontsize=14)
ax.set_xlabel("Value")
ax.set_ylabel("Frequency")
ax.axvline(data.mean(), color="#EB5757", linestyle="--", label=f"Mean = {data.mean():.3f}")
ax.legend()
plt.tight_layout()
plt.show()
print(f"Histogram rendered. Mean={data.mean():.4f}, Std={data.std():.4f}")

# %% Section 4: Scatter Plot — Correlation
x_scatter = np.random.randn(150)
y_scatter = 2.5 * x_scatter + np.random.randn(150) * 0.8

fig, ax = plt.subplots(figsize=(7, 5))
sc = ax.scatter(x_scatter, y_scatter, c=x_scatter, cmap="coolwarm",
                alpha=0.75, edgecolors="white", linewidths=0.4, s=60)
fig.colorbar(sc, ax=ax, label="x value")
ax.set_title("Scatter Plot with Correlation", fontsize=14)
ax.set_xlabel("x")
ax.set_ylabel("y = 2.5x + noise")
plt.tight_layout()
plt.show()
corr = np.corrcoef(x_scatter, y_scatter)[0, 1]
print(f"Scatter plot rendered. Pearson r = {corr:.4f}")

# %% Section 5: Multi-Panel Dashboard
fig = plt.figure(figsize=(11, 7))
gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.35)

# Panel A: Bar chart
ax1 = fig.add_subplot(gs[0, 0])
categories = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
values = np.random.randint(20, 100, len(categories))
bars = ax1.bar(categories, values, color=["#4A90D9", "#E57373", "#6FCF97", "#F2994A", "#BB6BD9"])
ax1.set_title("Bar Chart")
ax1.set_ylabel("Value")
for bar, val in zip(bars, values):
    ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
             str(val), ha="center", va="bottom", fontsize=9)

# Panel B: Pie chart
ax2 = fig.add_subplot(gs[0, 1])
sizes = [28, 22, 18, 15, 17]
labels = ["Core", "GUI", "Parser", "PDF", "Tests"]
ax2.pie(sizes, labels=labels, autopct="%1.0f%%",
        colors=["#4A90D9", "#E57373", "#6FCF97", "#F2994A", "#BB6BD9"],
        startangle=90, wedgeprops={"edgecolor": "white", "linewidth": 1.5})
ax2.set_title("Module Breakdown")

# Panel C: Heatmap
ax3 = fig.add_subplot(gs[1, 0])
matrix = np.random.rand(6, 6)
im = ax3.imshow(matrix, cmap="YlOrRd", aspect="auto")
fig.colorbar(im, ax=ax3)
ax3.set_title("Random Heatmap")
ax3.set_xticks(range(6))
ax3.set_yticks(range(6))

# Panel D: Step function
ax4 = fig.add_subplot(gs[1, 1])
t = np.linspace(0, 2 * np.pi, 100)
step_data = np.sign(np.sin(t))
ax4.step(t, step_data, color="#4A90D9", linewidth=2, where="mid")
ax4.fill_between(t, step_data, alpha=0.15, color="#4A90D9", step="mid")
ax4.set_title("Square Wave")
ax4.set_xlabel("t")
ax4.set_ylim(-1.5, 1.5)
ax4.grid(True, alpha=0.3)

fig.suptitle("Multi-Panel Dashboard", fontsize=16, fontweight="bold")
plt.show()
print("Multi-panel dashboard rendered.")
